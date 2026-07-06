from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import yaml

from orum.accounting import account_returns
from orum.dsl import backtest as dsl_backtest
from orum.dsl.diff import structural_diff
from orum.dsl.migrate import is_dsl_strategy, migrate_strategy_file, risk_value, strategy_dsl_groups
from orum.dsl.schema import DslValidationError, validate_strategy_dsl
from orum.fsio import atomic_write_text
from orum.paths import GOAL_PATH, HISTORY_DIR, HYPOTHESES_PATH, STATE_DIR, STRATEGY_PATH, TRADES_PATH
from orum.score import score

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_0RUM_HOME = ROOT_DIR / ".sandbox" / "0rum-local-llm-home"
LOCK_PATH = STATE_DIR / ".reflect.lock"
LOCK_STALE_SECONDS = 300.0


@contextmanager
def _reflection_lock():
    """Single-instance lock shared by the watcher and the dashboard endpoint.

    Two concurrent reflections double-bump the strategy version and clobber
    each other's history archive. Stale locks (crashed reflection) expire
    after LOCK_STALE_SECONDS.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if LOCK_PATH.exists() and time.time() - LOCK_PATH.stat().st_mtime > LOCK_STALE_SECONDS:
            LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit("another reflection is already in progress (state/.reflect.lock)") from None
    try:
        os.write(fd, f"{os.getpid()}\n".encode())
        os.close(fd)
        yield
    finally:
        LOCK_PATH.unlink(missing_ok=True)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _load_jsonl(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _bump_version(version: str) -> str:
    return f"{int(version) + 1:02d}"


def _save_change(strategy: dict, hypothesis: dict) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    version = strategy.get("version", "01")
    # Archive the on-disk strategy: `strategy` was already mutated in memory,
    # so dumping it here would record the post-change values under the
    # pre-change version number.
    if STRATEGY_PATH.exists():
        atomic_write_text(HISTORY_DIR / f"v{int(version):04d}.yaml", STRATEGY_PATH.read_text())
    else:
        atomic_write_text(HISTORY_DIR / f"v{int(version):04d}.yaml", yaml.safe_dump(strategy, sort_keys=False))
    strategy["version"] = _bump_version(version)
    atomic_write_text(STRATEGY_PATH, yaml.safe_dump(strategy, sort_keys=False))
    with HYPOTHESES_PATH.open("a") as handle:
        handle.write(json.dumps(hypothesis, sort_keys=True) + "\n")


def _trades_after(trades: list[dict], ts: str | None) -> int:
    if not ts:
        return 0
    return sum(1 for trade in trades if str(trade.get("ts", "")) > ts)


def _last_changed_hypothesis(hypotheses: list[dict]) -> dict | None:
    return next((item for item in reversed(hypotheses) if item.get("changed")), None)


def _prior_issue_count(hypotheses: list[dict], issue: str) -> int:
    return sum(1 for item in hypotheses if item.get("issue") == issue)


def _cooldown_status(goal: dict, trades: list[dict], hypotheses: list[dict]) -> dict | None:
    """Cooldown info when a recent strategy change still needs observation trades.

    goal.yaml mandates cooldown_after_change_trades for every reflection mode;
    both the deterministic fallback and the 0rum LLM path must respect it."""
    cooldown = int(goal.get("cooldown_after_change_trades", 0))
    last_change = _last_changed_hypothesis(hypotheses)
    if not (cooldown and last_change):
        return None
    closed_since_change = _trades_after(trades, last_change.get("ts"))
    if closed_since_change >= cooldown:
        return None
    remaining = cooldown - closed_since_change
    return {
        "issue": "cooldown",
        "reason": (
            f"cooldown active after {last_change.get('variable', 'strategy')} change; "
            f"{remaining} more closed trades required"
        ),
    }


def _worst_daily_return(trades: list[dict], returns: list[float]) -> float:
    """Worst single-day sum of account returns (daily_loss_limit is a daily
    figure; comparing it to one trade was both too lax and mislabelled)."""
    daily: dict[str, float] = {}
    for trade, item in zip(trades, returns):
        day = str(trade.get("ts", ""))[:10]
        daily[day] = daily.get(day, 0.0) + item
    return min(daily.values()) if daily else 0.0


def _fallback(strategy: dict, goal: dict, trades: list[dict], hypotheses: list[dict] | None = None) -> dict:
    hypotheses = hypotheses or []
    current_score = score(trades, goal)
    returns = account_returns(trades, goal)
    realised = sum(returns)
    worst_day = _worst_daily_return(trades, returns)

    hypothesis = {
        "ts": _now(),
        "mode": "fallback",
        "score": current_score,
        "changed": False,
        "variable": None,
        "reason": "insufficient evidence or no justified adjustment",
    }

    if len(trades) < int(goal.get("reflection_every", 10)):
        return hypothesis

    cooldown_block = _cooldown_status(goal, trades, hypotheses)
    if cooldown_block:
        hypothesis.update(cooldown_block)
        return hypothesis

    candidate: tuple[str, str, str] | None = None
    if worst_day <= -float(goal.get("daily_loss_limit", 0.015)):
        candidate = (
            "daily_loss_guardrail",
            "position_size_r",
            "daily loss guardrail was touched; reduce risk before seeking return",
        )
    elif realised < 0 and current_score < 0:
        candidate = (
            "negative_realised_weak_score",
            "entry.threshold",
            "negative realised return with weak score; require a stronger oversold signal",
        )

    if not candidate:
        return hypothesis

    issue, variable, reason = candidate
    evidence_required = int(goal.get("evidence_reflections_required", 1))
    observed_count = _prior_issue_count(hypotheses, issue) + 1
    if observed_count < evidence_required:
        hypothesis.update(
            issue=issue,
            variable=variable,
            reason=f"evidence {observed_count}/{evidence_required} for {reason}",
        )
        return hypothesis

    if variable == "position_size_r":
        # Risk fields live outside the DSL; the deterministic fallback may
        # still derisk, the LLM may not.
        risk = strategy.setdefault("risk", {})
        before = float(risk.get("position_size_r", strategy.get("position_size_r", 0.5)))
        after = max(float(goal.get("risk_per_trade_min", 0.005)) * 100, before - 0.1)
        if after == before:
            hypothesis.update(issue=issue, variable=variable, reason="position_size_r already at minimum")
            return hypothesis
        risk["position_size_r"] = after
        strategy.pop("position_size_r", None)
    elif variable == "entry.threshold":
        # Reformulated as a DSL mutation that only nudges one `value`: the
        # fallback stays a parameter nudge, that is its strength.
        condition = _entry_rsi_condition(strategy)
        if condition is None:
            hypothesis.update(
                issue=issue,
                variable=variable,
                reason="entry group has no oversold rsi condition to tighten; structural change is the LLM's job",
            )
            return hypothesis
        before = float(condition.get("value", 30))
        after = max(10, before - 2)
        if after == before:
            hypothesis.update(issue=issue, variable=variable, reason="entry.threshold already at floor")
            return hypothesis
        condition["value"] = after

    hypothesis.update(changed=True, issue=issue, variable=variable, reason=reason)
    return hypothesis


def _entry_rsi_condition(strategy: dict) -> dict | None:
    """The oversold rsi condition of a DSL-layout strategy's entry group."""
    if not is_dsl_strategy(strategy):
        return None
    for condition in strategy.get("entry", {}).get("conditions", []):
        if condition.get("indicator") == "rsi" and condition.get("operator") in ("<", "<="):
            return condition
    return None


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def _reject(hypothesis: dict, reason: str) -> dict:
    hypothesis.update(changed=False, rejected=True, reason=reason)
    return hypothesis


def _coerce_regime_values(group: dict) -> None:
    """Salvage the common LLM serialization slip on regime conditions.

    The schema already supports a categorical `regime` indicator, but its label
    belongs in `value_str` (string), not the numeric `value` field. Models that
    have only ever seen numeric conditions tend to emit
    {"indicator": "regime", "operator": "!=", "value": "unfavorable"}, which the
    schema rejects as 'unfavorable' is not of type 'number'. We move a stray
    string `value` into `value_str` so the deterministic validators can then do
    their job. We never touch numeric values, and never overwrite an explicit
    value_str — anything still malformed is left for validation to reject.
    """
    for condition in group.get("conditions") or []:
        if not isinstance(condition, dict):
            continue
        if condition.get("indicator") != "regime":
            continue
        if "value_str" in condition:
            continue
        value = condition.get("value")
        if isinstance(value, str):
            condition["value_str"] = value
            del condition["value"]


def _apply_dsl_hypothesis(strategy: dict, hypothesis: dict, goal: dict) -> dict:
    """Validation chain for an LLM structural mutation; the model output is
    untrusted data, every step can reject. Order per spec: jsonschema,
    semantic validation, structural diff limit, non-regression backtest,
    cooldown (checked before the LLM call)."""
    action = hypothesis.get("action")
    if action == "no_change":
        hypothesis.update(changed=False, reason=hypothesis.get("rationale", "model proposed no change"))
        return hypothesis
    if action != "change":
        return _reject(hypothesis, f"rejected: unknown action {action!r}")

    proposed_entry = hypothesis.get("proposed_entry")
    proposed_exit = hypothesis.get("proposed_exit")
    if not isinstance(proposed_entry, dict) or not isinstance(proposed_exit, dict):
        return _reject(hypothesis, "rejected: action=change requires proposed_entry and proposed_exit objects")

    # Step 0: salvage regime label slips (value -> value_str) before validation,
    # so a near-correct mutation is not lost to a field-name mistake.
    _coerce_regime_values(proposed_entry)
    _coerce_regime_values(proposed_exit)

    # Steps 1+2: jsonschema then semantic validation (whitelist, bounds, warm-up).
    try:
        validate_strategy_dsl(proposed_entry, proposed_exit)
    except DslValidationError as exc:
        return _reject(hypothesis, f"rejected by DSL validation: {'; '.join(exc.errors)}")

    # Step 3: structural diff limit (one_block_only, successor of one_variable_only).
    current = strategy_dsl_groups(strategy)
    changes = structural_diff(current["entry"], current["exit"], proposed_entry, proposed_exit)
    if changes == 0:
        hypothesis.update(changed=False, reason="proposed strategy is identical to the current one")
        return hypothesis
    if goal.get("one_block_only", True) and changes > 1:
        return _reject(
            hypothesis,
            f"rejected: {changes} structural changes proposed but one_block_only allows a single added/removed/modified condition",
        )

    # Step 4: non-regression backtest over recent 1m candles.
    backtest_block = _backtest_guard(strategy, proposed_entry, proposed_exit, goal)
    if backtest_block:
        return _reject(hypothesis, backtest_block)

    strategy["entry"] = proposed_entry
    strategy["exit"] = proposed_exit
    hypothesis.update(
        changed=True,
        variable="dsl.entry+exit",
        structural_changes=changes,
        reason=hypothesis.get("rationale", "structural DSL mutation"),
    )
    return hypothesis


BACKTEST_DAYS = 7
DEGENERACY_FACTOR = 10


def _backtest_guard(strategy: dict, proposed_entry: dict, proposed_exit: dict, goal: dict) -> str | None:
    """Step 4: replay proposed vs current strategy on recent 1m candles.

    Fail closed: no data means no mutation. Rejections per spec: zero entry
    signals, simulated daily_loss_limit breach, or a trade count more than
    10x the current strategy's (degeneracy)."""
    asset = goal.get("asset", "BTC/USDT")
    try:
        candles = dsl_backtest.load_history(asset, days=BACKTEST_DAYS)
    except Exception as exc:  # noqa: BLE001 - any data failure must block, not crash, the reflection.
        return f"rejected: backtest history unavailable ({exc})"

    risk = {
        key: risk_value(strategy, key, default)
        for key, default in (
            ("stop_loss_pct", 2.0),
            ("take_profit_pct", 3.0),
            ("max_hold_candles", 30),
            ("position_size_r", 0.5),
            ("fee_rate", 0.0004),
        )
    }
    current = strategy_dsl_groups(strategy)
    proposed = dsl_backtest.simulate({"entry": proposed_entry, "exit": proposed_exit}, risk, goal, candles)
    baseline = dsl_backtest.simulate(current, risk, goal, candles)

    if proposed["entries_triggered"] == 0:
        return f"rejected: proposed strategy fired zero entry signals over the {BACKTEST_DAYS}-day backtest window"
    daily_loss_limit = float(goal.get("daily_loss_limit", 0.015))
    if proposed["worst_day"] <= -daily_loss_limit:
        return (
            f"rejected: backtest worst day {proposed['worst_day']:.4f} breaches "
            f"daily_loss_limit {daily_loss_limit} (simulated)"
        )
    if len(proposed["trades"]) > DEGENERACY_FACTOR * max(1, len(baseline["trades"])):
        return (
            f"rejected: degenerate trade count {len(proposed['trades'])} vs current "
            f"{len(baseline['trades'])} over the backtest window (>{DEGENERACY_FACTOR}x)"
        )
    return None


def _regime_stats(trades: list[dict]) -> dict:
    """Trade outcomes grouped by market regime at entry."""
    stats: dict[str, dict] = {}
    for trade in trades:
        label = str(trade.get("market_regime_at_entry", "unknown"))
        bucket = stats.setdefault(label, {"trades": 0, "wins": 0, "net_pnl_usd": 0.0})
        bucket["trades"] += 1
        net = float(trade.get("net_pnl_usd", 0.0))
        bucket["net_pnl_usd"] += net
        if net > 0:
            bucket["wins"] += 1
    for bucket in stats.values():
        bucket["net_pnl_usd"] = round(bucket["net_pnl_usd"], 2)
    return stats


OUTPUT_CONTRACT = """{
  "action": "change" | "no_change",
  "proposed_entry": { "logic": "AND" | "OR", "conditions": [ ... ] },
  "proposed_exit":  { "logic": "AND" | "OR", "conditions": [ ... ] },
  "rationale": "justification ancrée dans les trades fournis",
  "expected_effect": "métrique attendue et horizon",
  "issue": "étiquette du problème adressé"
}"""


def _orum_prompt(strategy: dict, goal: dict, trades: list[dict], hypotheses: list[dict]) -> str:
    groups = strategy_dsl_groups(strategy)
    current_dsl = json.dumps({"entry": groups["entry"], "exit": groups["exit"]}, sort_keys=True)
    recent_trades = trades[-30:]
    window = int(goal.get("reflection_every", 10))
    return f"""
Tu es le module de réflexion d'un bot de paper-trading BTC/USDT (bougies 1m, long-only).
Tu peux proposer UNE modification structurelle de la stratégie, exprimée UNIQUEMENT dans
le format JSON ci-dessous. Tu ne peux PAS toucher au risque (stop, taille, limites) ni à
la direction.

STRATÉGIE COURANTE (JSON) :
{current_dsl}

CATALOGUE AUTORISÉ :
- indicateurs : rsi(period 2-50), sma(period 2-200), ema(period 2-200), close,
  bollinger(period 5-50, std_dev 1-3 ; fields upper/middle/lower/pct_b), atr(period 2-50),
  regime (labels: favorable|neutral|unfavorable ; opérateurs == / != seulement)
- opérateurs : >, <, >=, <=, crosses_above, crosses_below, rising, falling, between
- max 4 conditions par groupe, logic AND ou OR, pas d'imbrication
- au plus UNE condition ajoutée/supprimée/modifiée par rapport à la stratégie courante

FORME D'UNE CONDITION (champs selon l'indicateur) :
- indicateur numérique (rsi, sma, ema, close, bollinger, atr) : la valeur comparée
  va dans `value` (un nombre), et `value2` pour `between`. Ex :
  {{"indicator": "rsi", "params": {{"period": 14}}, "operator": "<=", "value": 25}}
- `regime` : la valeur va dans `value_str` (une chaîne parmi favorable|neutral|unfavorable),
  opérateurs == ou != UNIQUEMENT, jamais de champ `value`. Ex :
  {{"indicator": "regime", "operator": "!=", "value_str": "unfavorable"}}

DONNÉES (tu dois justifier toute modification à partir d'elles ; sinon réponds no_change) :
- 30 derniers trades fermés : {json.dumps(recent_trades, sort_keys=True)}
- score courant et historique : {json.dumps({"current": score(trades, goal), "recent_hypotheses_scores": [h.get("score") for h in hypotheses[-10:]]}, sort_keys=True)}
- répartition des trades par régime de marché : {json.dumps(_regime_stats(recent_trades), sort_keys=True)}
- hypothèses passées et leurs effets mesurés : {json.dumps(hypotheses[-10:], sort_keys=True)}

RÈGLES :
- "no_change" est une réponse de qualité si les données ne justifient rien.
- Une modification = une hypothèse falsifiable : indique la métrique attendue et l'horizon
  (en trades) dans expected_effect.
- N'optimise pas pour les {window} derniers trades exactement (sur-ajustement) : la justification
  doit invoquer un mécanisme (ex. « les pertes se concentrent en régime défavorable »),
  pas une coïncidence.

Réponds avec UN SEUL objet JSON, sans markdown :
{OUTPUT_CONTRACT}
""".strip()


OLLAMA_CHAT_URL = os.getenv("0RUM_OLLAMA_URL", "http://127.0.0.1:11434/v1/chat/completions")
REFLECT_MODEL = os.getenv("0RUM_REFLECT_MODEL", "gemma4:31b")
REFLECT_FALLBACK_MODEL = os.getenv("0RUM_REFLECT_FALLBACK_MODEL", "gemma4:latest")


def _ollama_chat(model: str, prompt: str, timeout: float) -> str:
    """One-shot completion from a local Ollama model (OpenAI-compatible API)."""
    import httpx

    resp = httpx.post(
        OLLAMA_CHAT_URL,
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "stream": False,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _reflect_completion(prompt: str) -> tuple[str, str]:
    """Local-only reflection: primary model, then a lighter fallback on error.

    Returns (raw_text, model_name). No dependency on the external `0rum`
    agent — the reflection talks straight to Ollama on this machine.
    """
    try:
        return _ollama_chat(REFLECT_MODEL, prompt, timeout=300), REFLECT_MODEL
    except Exception:
        return _ollama_chat(REFLECT_FALLBACK_MODEL, prompt, timeout=180), REFLECT_FALLBACK_MODEL


def _orum(strategy: dict, goal: dict, trades: list[dict], hypotheses: list[dict]) -> dict:
    cooldown_block = _cooldown_status(goal, trades, hypotheses)
    if cooldown_block:
        return {
            "ts": _now(),
            "mode": "0rum",
            "score": score(trades, goal),
            "changed": False,
            "variable": None,
            "new_value": None,
            **cooldown_block,
        }

    prompt = _orum_prompt(strategy, goal, trades, hypotheses)
    text, model_used = _reflect_completion(prompt)
    raw = _extract_json(text)
    # Rebuild the record from whitelisted fields only: the model output is
    # untrusted and must not smuggle arbitrary keys into hypotheses.jsonl.
    hypothesis = {
        "ts": _now(),
        "mode": "0rum",
        "score": score(trades, goal),
        "model": model_used,
        "provider": "ollama",
        "action": raw.get("action"),
        "proposed_entry": raw.get("proposed_entry"),
        "proposed_exit": raw.get("proposed_exit"),
        "rationale": raw.get("rationale"),
        "expected_effect": raw.get("expected_effect"),
        "issue": raw.get("issue"),
    }
    return _apply_dsl_hypothesis(strategy, hypothesis, goal)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fallback", action="store_true")
    parser.add_argument("--0rum", dest="orum", action="store_true")
    args = parser.parse_args()
    if args.fallback == args.orum:
        raise SystemExit("choose exactly one mode: --fallback or --0rum")

    with _reflection_lock():
        goal = yaml.safe_load(GOAL_PATH.read_text()) or {}
        strategy = yaml.safe_load(STRATEGY_PATH.read_text()) or {}
        if not is_dsl_strategy(strategy):
            # Reflection always reasons on the DSL layout; the worker performs
            # the same migration at boot, whichever runs first wins.
            strategy = migrate_strategy_file(strategy)
        trades = _load_jsonl(TRADES_PATH)
        hypotheses = _load_jsonl(HYPOTHESES_PATH)
        hypothesis = (
            _fallback(strategy, goal, trades, hypotheses) if args.fallback else _orum(strategy, goal, trades, hypotheses)
        )

        if hypothesis.get("changed"):
            _save_change(strategy, hypothesis)
        else:
            with HYPOTHESES_PATH.open("a") as handle:
                handle.write(json.dumps(hypothesis, sort_keys=True) + "\n")
    print(json.dumps(hypothesis, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
