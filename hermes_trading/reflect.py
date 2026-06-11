from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import yaml

from hermes_trading.accounting import account_returns
from hermes_trading.fsio import atomic_write_text
from hermes_trading.paths import GOAL_PATH, HISTORY_DIR, HYPOTHESES_PATH, STATE_DIR, STRATEGY_PATH, TRADES_PATH
from hermes_trading.score import score

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_HERMES_HOME = ROOT_DIR / ".sandbox" / "hermes-local-llm-home"
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
    both the deterministic fallback and the Hermes LLM path must respect it."""
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


def _fallback(strategy: dict, goal: dict, trades: list[dict], hypotheses: list[dict] | None = None) -> dict:
    hypotheses = hypotheses or []
    current_score = score(trades, goal)
    returns = account_returns(trades, goal)
    realised = sum(returns)
    worst_trade = min(returns) if returns else 0.0

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
    if worst_trade <= -float(goal.get("daily_loss_limit", 0.015)):
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
        before = float(strategy.get("position_size_r", 0.5))
        after = max(float(goal.get("risk_per_trade_min", 0.005)) * 100, before - 0.1)
        if after == before:
            hypothesis.update(issue=issue, variable=variable, reason="position_size_r already at minimum")
            return hypothesis
        strategy["position_size_r"] = after
    elif variable == "entry.threshold":
        entry = strategy.setdefault("entry", {})
        before = float(entry.get("threshold", 30))
        after = max(10, before - 2)
        if after == before:
            hypothesis.update(issue=issue, variable=variable, reason="entry.threshold already at floor")
            return hypothesis
        entry["threshold"] = after

    hypothesis.update(changed=True, issue=issue, variable=variable, reason=reason)
    return hypothesis


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


# Hard bounds enforced in code; the prompt states the same limits, but the
# model output is untrusted and must not be applied verbatim.
VARIABLE_BOUNDS = {
    "entry.threshold": (10.0, 45.0),
    "stop_loss_pct": (0.5, 5.0),
    "position_size_r": (0.5, 0.75),
}


def _reject(hypothesis: dict, reason: str) -> dict:
    hypothesis.update(changed=False, rejected=True, reason=reason)
    return hypothesis


def _apply_hypothesis(strategy: dict, hypothesis: dict) -> dict:
    if not hypothesis.get("changed"):
        return hypothesis

    variable = hypothesis.get("variable")
    if variable not in VARIABLE_BOUNDS:
        return _reject(hypothesis, f"rejected: unsupported Hermes variable {variable!r}")

    raw_value = hypothesis.get("new_value")
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return _reject(hypothesis, f"rejected: new_value {raw_value!r} is not a number")
    if math.isnan(value):
        return _reject(hypothesis, "rejected: new_value is NaN")

    lower, upper = VARIABLE_BOUNDS[variable]
    clamped = min(upper, max(lower, value))
    if clamped != value:
        hypothesis["requested_value"] = value
        hypothesis["reason"] = (
            f"clamped {variable} from {value} to [{lower}, {upper}]; {hypothesis.get('reason', '')}".strip()
        )
    hypothesis["new_value"] = clamped

    if variable == "entry.threshold":
        strategy.setdefault("entry", {})["threshold"] = clamped
    elif variable == "stop_loss_pct":
        strategy["stop_loss_pct"] = clamped
    elif variable == "position_size_r":
        strategy["position_size_r"] = clamped
    return hypothesis


def _hermes_prompt(strategy: dict, goal: dict, trades: list[dict], hypotheses: list[dict]) -> str:
    payload = {
        "goal": goal,
        "strategy": strategy,
        "latest_trades": trades[-25:],
        "recent_hypotheses": hypotheses[-10:],
        "score": score(trades, goal),
    }
    return f"""
You are Hermes, the reflection brain of a self-improving paper trading agent.

You are not placing orders. You are reviewing outcomes and may update at most
one strategy variable.

Return JSON only. No markdown. No prose outside JSON.

Allowed variables:
- entry.threshold
- stop_loss_pct
- position_size_r

Schema:
{{
  "changed": true | false,
  "variable": "entry.threshold" | "stop_loss_pct" | "position_size_r" | null,
  "new_value": number | null,
  "reason": "short reason",
  "confidence": number
}}

Rules:
- Change at most one variable.
- If evidence is weak, return changed=false.
- Use market_regime_at_entry and market_regime_at_exit as outcome context only.
- Do not invent new indicators or hidden entry filters outside strategy.yaml.
- Do not chase the +7% target by simply increasing risk.
- Keep position_size_r between 0.5 and 0.75.
- Keep entry.threshold between 10 and 45.
- Keep stop_loss_pct between 0.5 and 5.0.

State:
{json.dumps(payload, sort_keys=True)}
""".strip()


def _hermes(strategy: dict, goal: dict, trades: list[dict], hypotheses: list[dict]) -> dict:
    cooldown_block = _cooldown_status(goal, trades, hypotheses)
    if cooldown_block:
        return {
            "ts": _now(),
            "mode": "hermes",
            "score": score(trades, goal),
            "changed": False,
            "variable": None,
            "new_value": None,
            **cooldown_block,
        }

    prompt = _hermes_prompt(strategy, goal, trades, hypotheses)
    hermes_home = Path(os.getenv("HERMES_REFLECT_HOME", str(DEFAULT_HERMES_HOME)))
    env = os.environ.copy()
    env.setdefault("HERMES_HOME", str(hermes_home))
    result = subprocess.run(
        ["hermes", "-z", prompt, "--ignore-rules"],
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=180,
    )
    hypothesis = _extract_json(result.stdout)
    model_cfg = yaml.safe_load((hermes_home / "config.yaml").read_text()).get("model", {})
    hypothesis.update(
        {
            "ts": _now(),
            "mode": "hermes",
            "score": score(trades, goal),
            "model": model_cfg.get("default", "unknown"),
            "provider": model_cfg.get("provider", "unknown"),
        }
    )
    return _apply_hypothesis(strategy, hypothesis)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fallback", action="store_true")
    parser.add_argument("--hermes", action="store_true")
    args = parser.parse_args()
    if args.fallback == args.hermes:
        raise SystemExit("choose exactly one mode: --fallback or --hermes")

    with _reflection_lock():
        goal = yaml.safe_load(GOAL_PATH.read_text()) or {}
        strategy = yaml.safe_load(STRATEGY_PATH.read_text()) or {}
        trades = _load_jsonl(TRADES_PATH)
        hypotheses = _load_jsonl(HYPOTHESES_PATH)
        hypothesis = (
            _fallback(strategy, goal, trades, hypotheses) if args.fallback else _hermes(strategy, goal, trades, hypotheses)
        )

        if hypothesis.get("changed"):
            _save_change(strategy, hypothesis)
        else:
            with HYPOTHESES_PATH.open("a") as handle:
                handle.write(json.dumps(hypothesis, sort_keys=True) + "\n")
    print(json.dumps(hypothesis, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
