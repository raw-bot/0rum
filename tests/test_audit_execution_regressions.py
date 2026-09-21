"""Regression guarantees from the 2026-09-05 execution audit.

Run only through isolated_runner.py against the copied bot. All data are synthetic.
"""
import json
from pathlib import Path
from datetime import datetime, timezone

import pytest

from orum.portfolio.paper_broker import Account, PaperBroker, Position
from orum.portfolio.paper_engine import PaperEngine
from orum.portfolio.position_manager import DynamicExitPolicy, DynamicExitState
from orum.strategies import register_engine, _ENGINES
from orum.strategies.base import Side, Signal

START = 1_788_000_000_000
STEP = 900_000


class SyntheticEngine:
    name = "audit_execution_signal"
    version = "audit"
    required_timeframes = []
    required_indicators = []
    warmup_period = 1

    def init(self, config):
        self.mode = config.get("mode", "long")
        self.distance = float(config.get("distance", 10))

    def on_candle(self, candle, context):
        if self.mode == "error":
            raise ValueError("synthetic strategy failure")
        if self.mode == "hold":
            return None
        return Signal(Side.LONG, context.symbol, context.timeframe,
                      "synthetic audit", suggested_stop=100 - self.distance)


@pytest.fixture(autouse=True)
def registry():
    register_engine(SyntheticEngine.name, SyntheticEngine)
    yield
    _ENGINES.pop(SyntheticEngine.name, None)


def bars():
    return [dict(ts=START+i*STEP, open=100., high=102., low=98., close=100., volume=1.)
            for i in range(20)]


def strategy(sid="s", symbol="BTC/USDT", risk=.01, **params):
    return dict(id=sid, engine=SyntheticEngine.name, symbol=symbol,
                timeframe="15m", monitor_timeframe="15m", risk_pct=risk,
                exit_policy="signal_or_stop", params=params)


def engine(tmp_path, rows, strategies=None, **config):
    cfg = dict(starting_balance_usd=10000., reentry_policy="topup",
               risk_sizing_basis="actual_stop", strategies=strategies or [strategy()])
    cfg.update(config)
    return PaperEngine(cfg, candle_provider=lambda *_: [dict(x) for x in rows],
                       positions_path=tmp_path / "positions.json",
                       fills_path=tmp_path / "fills.jsonl", equity_path=tmp_path / "equity.jsonl")


def seed(tmp_path, position, balance=10000):
    (tmp_path / "positions.json").write_text(json.dumps(Account(balance, {position.strategy_id: position}).to_dict()))


def position(**kw):
    values = dict(strategy_id="s", symbol="BTC/USDT", side="long", qty=1,
                  entry_px=100, notional_usd=100, risk_pct=.01, atr_risk=10,
                  risk_distance=10, risk_sizing_basis="actual_stop",
                  stop_loss_price=90, last_monitor_candle_ts=START+18*STEP)
    values.update(kw)
    return Position(**values)


def test_EX1_next_open_protects_execution_bar(tmp_path):
    rows = bars()
    rows[-1].update(open=100., high=105., low=85., close=102.)
    e = engine(tmp_path, rows, execution_mode="next_open")
    now = datetime.fromtimestamp((rows[-1]["ts"]+STEP)/1000, timezone.utc)
    first = e.run_cycle(now=now)
    second = e.run_cycle(now=now)
    state = json.loads((tmp_path / "positions.json").read_text())
    assert [x["action"] for x in first["fills"]] == ["open", "close"]
    assert first["fills"][0]["price"] == 100
    assert first["fills"][0]["stop_loss_price"] == 90
    assert rows[-1]["low"] < 90
    assert state["positions"] == {}
    assert first["fills"][1]["price"] == 90
    assert second["fills"] == [] and second["open_positions"] == []


def test_EX2_static_stop_fills_at_gap_open(tmp_path):
    rows = bars()
    rows[-1].update(open=80., high=85., low=75., close=80.)
    seed(tmp_path, position())
    out = engine(tmp_path, rows, [strategy(mode="hold")]).run_cycle()
    fill = out["fills"][0]
    assert fill["reason"] == "stop_loss" and fill["price"] == 80
    assert rows[-1]["low"] <= fill["price"] <= rows[-1]["high"]


def test_positive_dynamic_stop_uses_gap_open(tmp_path):
    rows = bars()
    rows[-1].update(open=80., high=85., low=75., close=80.)
    policy = DynamicExitPolicy(mode="execute", version="mfe_ratchet_v1", close_on_ssl_invalidation=False)
    state = DynamicExitState(policy=policy, peak_favorable_price=110, mfe_r=1,
                             dynamic_stop_price=97, armed=True, last_candle_ts=START+18*STEP)
    seed(tmp_path, position(dynamic_exit=state.to_dict()))
    out = engine(tmp_path, rows, [strategy(mode="hold")]).run_cycle()
    assert out["fills"][0]["reason"] == "dynamic_stop"
    assert out["fills"][0]["price"] == 80


def test_EX3_configured_leverage_is_per_tranche_not_aggregate(tmp_path):
    strategies = [strategy("eth", "ETH/USDT", .005, distance=.1),
                  strategy("gold", "PAXG/USDT", .02, distance=.1),
                  strategy("nvda", "NVDA", .0025, distance=.1)]
    out = engine(tmp_path, bars(), strategies, max_leverage=3.,
                 max_total_stop_risk_pct=.05, max_symbol_stop_risk_pct=.03,
                 max_symbol_notional_pct={"BTC/USDT": 1.},
                 max_open_positions_by_symbol={"BTC/USDT": 1}).run_cycle()
    notionals = [f["notional_usd"] for f in out["fills"]]
    aggregate = sum(notionals)
    risk = sum(f["qty"]*f["risk_distance"] for f in out["fills"])
    assert len(notionals) == 3 and max(notionals) <= 30000
    assert aggregate > 8 * 10000 and risk < 500


def test_EX4_auction_reports_actual_risk_after_notional_cap(tmp_path):
    out = engine(tmp_path, bars(), [strategy(distance=1)],
                 max_symbol_notional_pct={"BTC/USDT": .5}).run_cycle()
    fill, grant = out["fills"][0], out["auction"][0]
    actual = fill["qty"]*fill["risk_distance"]
    assert grant["granted_risk_usd"] == 50 and grant["authorized_risk_usd"] == 100 and actual == 50
    assert grant["outcome"] == "granted_topup" and grant["grant_fraction"] == .5


def test_positive_drawdown_halt_keeps_protective_exits(tmp_path):
    rows = bars()
    rows[-1].update(open=95., high=100., low=85., close=95.)
    seed(tmp_path, position(), balance=8000)
    out = engine(tmp_path, rows,
                 entry_drawdown_risk_scale=dict(start_pct=.1, halt_pct=.2, floor_multiplier=.1)).run_cycle()
    assert out["fills"][0]["action"] == "close"
    assert out["fills"][0]["reason"] == "stop_loss"
    assert out["entry_drawdown_halt"] is True


def test_EX5_strategy_error_preserves_existing_position_protection(tmp_path):
    rows = bars()
    rows[-1].update(open=95., high=100., low=85., close=95.)
    seed(tmp_path, position())
    out = engine(tmp_path, rows, [strategy(mode="error")]).run_cycle()
    assert out["errors"] == {"s": "ValueError: synthetic strategy failure"}
    assert [fill["action"] for fill in out["fills"]] == ["close"] and out["open_positions"] == []
    assert rows[-1]["low"] <= 90


def test_EX6_static_close_consumes_new_primary_signal(tmp_path):
    rows = bars()
    rows[-1].update(open=100., high=102., low=85., close=100.)
    seed(tmp_path, position())
    seeded = json.loads((tmp_path / "positions.json").read_text())
    seeded["processed_candles"] = {"s": START + 17*STEP}
    (tmp_path / "positions.json").write_text(json.dumps(seeded))
    e = engine(tmp_path, rows, execution_mode="signal_close")
    first = e.run_cycle()
    after_close = json.loads((tmp_path / "positions.json").read_text())
    second = e.run_cycle()
    assert [f["action"] for f in first["fills"]] == ["close"]
    assert after_close["processed_candles"]["s"] == START + 19*STEP
    assert second["fills"] == []


def test_positive_broker_balance_charges_entry_and_exit_fees():
    a, b = Account(10000), PaperBroker(.001)
    opened = b.open(a, strategy_id="s", symbol="BTC/USDT", price=100,
                    atr_risk=10, risk_distance=10, risk_pct=.01,
                    equity_for_sizing=10000, risk_sizing_basis="actual_stop")
    closed = b.close(a, strategy_id="s", price=110)
    assert opened["fee_usd"] == .5 and closed["fee_usd"] == .55
    assert a.balance_usd == pytest.approx(10098.95)
    assert closed["realized_pnl_usd"] == pytest.approx(99.45)


@pytest.mark.parametrize('side,stop,target,opn,low,high', [('long',90,110,120,85,125), ('short',110,90,80,75,115)])
def test_gap_target_precedes_later_intrabar_stop(side, stop, target, opn, low, high):
    held = position(side=side, stop_loss_price=stop, take_profit_price=target)
    candle = dict(open=opn, low=low, high=high)
    assert PaperEngine._static_protective_exit(held, candle) == ('take_profit', opn)
    assert PaperEngine._protective_exit(held, candle) == ('take_profit', opn)


@pytest.mark.parametrize('fault', ['evaluate', 'audit'])
def test_dynamic_failure_preserves_earliest_retry_cursor(tmp_path, monkeypatch, fault):
    import orum.portfolio.paper_engine as module
    rows = bars()
    policy = DynamicExitPolicy(mode='observe', version='mfe_ratchet_v1', close_on_ssl_invalidation=False)
    state = DynamicExitState(policy=policy, peak_favorable_price=100, mfe_r=0,
                             last_candle_ts=START+17*STEP)
    seed(tmp_path, position(dynamic_exit=state.to_dict(), last_monitor_candle_ts=START+17*STEP))
    bot = engine(tmp_path, rows, [strategy(mode='hold')])
    seen = []
    evaluate = module.evaluate_dynamic_exit
    def tracking(**kw):
        seen.append(kw['candle']['ts'])
        return evaluate(**kw)
    def fail(*args, **kw): raise OSError('temporary failure')
    with monkeypatch.context() as m:
        m.setattr(module, 'evaluate_dynamic_exit', fail if fault=='evaluate' else tracking)
        if fault=='audit': m.setattr(bot, '_record_dynamic_decision', fail)
        assert bot.run_cycle()['errors']
    assert bot._load_account().positions['s'].last_monitor_candle_ts == START+17*STEP
    monkeypatch.setattr(module, 'evaluate_dynamic_exit', tracking)
    bot.run_cycle()
    assert START+18*STEP in seen
    assert bot._load_account().positions['s'].last_monitor_candle_ts == START+19*STEP


def test_next_open_rejects_account_already_after_entry_boundary(tmp_path):
    bot = engine(tmp_path, bars(), execution_mode='next_open')
    account = Account(10000)
    account.last_event_ts = START+19*STEP+1
    bot._save_account(account)
    result = bot.run_cycle()
    assert 'execution_timeline' in result['errors'] and result['fills'] == []


def test_shadow_only_timeframe_failure_does_not_block_entry(tmp_path):
    bot = engine(tmp_path, bars(), shadow_regime_filters={'BTC/USDT':{'timeframe':'4h','ema_period':2}})
    provider = bot._provider
    def fetch(symbol, timeframe, limit):
        if timeframe=='4h': raise OSError('observer source failed')
        return provider(symbol,timeframe,limit)
    bot._provider = fetch
    result = bot.run_cycle()
    assert any('shadow' in key for key in result['errors'])
    assert [fill['action'] for fill in result['fills']] == ['open']


def test_next_open_existing_gap_frees_cap_before_replacement(tmp_path):
    rows=bars(); rows[-1].update(open=85,high=88,low=84,close=86)
    seed(tmp_path,position())
    bot=engine(tmp_path,rows,[strategy(distance=20)],execution_mode='next_open',reentry_policy='hold',max_open_positions_by_symbol={'BTC/USDT':1})
    result=bot.run_cycle()
    assert [fill['action'] for fill in result['fills']]==['close','open']
    assert result['fills'][0]['price']==85 and result['fills'][0]['gap_at_open']
    assert result['fills'][1]['price']==85 and result['fills'][1]['stop_loss_price']==80
