"""Timing, data and operator-surface guarantees from the 8787 audit."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from orum import dashboard
from orum.market_calendar import session_bounds
from orum.portfolio.paper_broker import Account, Position
from orum.strategies.base import StrategyContext, Side
from orum.strategies.cot_gate import read_gate
from orum.strategies.cot_calendar import publication_at
from orum.strategies.opening_range import OpeningRangeEngine
from scripts import run_paper_portfolio
from scripts.replay_harness.reprice import reprice
from scripts.replay_harness.timeline import SnapshotProvider
from test_audit_execution_regressions import engine, bars, strategy, STEP, registry

UTC = timezone.utc
NY = ZoneInfo('America/New_York')
NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)


def test_observed_mark_does_not_backdate_fill_into_closed_bar(tmp_path):
    rows = bars()
    rows[-1].update(open=100., high=110., low=80., close=105.)
    bot = engine(tmp_path, rows, execution_mode='observed_mark')
    at = datetime.fromtimestamp((rows[-1]['ts'] + STEP) / 1000, UTC)
    result = bot.run_cycle(now=at)
    assert [row['action'] for row in result['fills']] == ['open']
    fill = result['fills'][0]
    assert fill['price'] == 105
    assert fill['price_asof_ts'] == int(at.timestamp() * 1000)
    assert fill['execution_method'] == 'observed_mark_v2'
    assert result['auction'][0]['executed_risk_usd'] == pytest.approx(fill['qty'] * fill['risk_distance'])
    assert bot.run_cycle(now=at)['fills'] == []


def test_observed_market_watermark_survives_restart(tmp_path):
    rows = bars()
    at = datetime.fromtimestamp((rows[-1]['ts'] + STEP) / 1000, UTC)
    engine(tmp_path, rows, execution_mode='observed_mark').run_cycle(now=at)
    older = rows[:-1]
    result = engine(tmp_path, older, execution_mode='observed_mark').run_cycle(now=at)
    assert result['fills'] == []
    assert any('backwards' in value for value in result['errors'].values())
    assert result['valuation_complete'] is False


def test_reprice_first_bar_stop_and_round_trip_fees():
    provider = SnapshotProvider()
    t0 = int(NOW.timestamp() * 1000)
    provider.series[('BTC/USDT', '15m')] = [
        {'ts': t0, 'open': 100, 'high': 110, 'low': 80, 'close': 105},
        {'ts': t0+STEP, 'open': 110, 'high': 110, 'low': 110, 'close': 110},
    ]
    trade = dict(sid='s', position_id='s', side='long', open_ts=t0, legacy_entry=100,
                 stop=90, tp=None, atr_risk=10, risk_distance=10, risk_pct=.02,
                 close_ts=t0+STEP, close_reason='signal', legacy_exit=110)
    result = reprice([trade], provider, symbol='BTC/USDT', slippage_bps=0, max_leverage=None)
    assert result['final_equity'] == 9798.10


@pytest.mark.parametrize('fault', ['nan', 'duplicate', 'gap', 'reverse', 'negative_volume', 'ohlc', 'stale'])
def test_active_binance_provider_rejects_bad_batches(fault):
    now = 900 * 30
    raw = [{'time': i*900, 'open': 100, 'high': 101, 'low': 99, 'close': 100, 'volume': 1} for i in range(30)]
    if fault == 'nan': raw[2]['close'] = float('nan')
    if fault == 'duplicate': raw[2]['time'] = raw[1]['time']
    if fault == 'gap': raw.pop(2)
    if fault == 'reverse': raw[1], raw[2] = raw[2], raw[1]
    if fault == 'negative_volume': raw[2]['volume'] = -1
    if fault == 'ohlc': raw[2]['high'] = 50
    if fault == 'stale': now += 900*3
    with patch.object(run_paper_portfolio, 'fetch_klines', return_value=raw), patch.object(run_paper_portfolio.time, 'time', return_value=now):
        with pytest.raises(ValueError):
            run_paper_portfolio.binance_provider('BTC/USDT', '15m', 300)


def gate_file(tmp_path, report='2026-09-01'):
    path = tmp_path/'gate.json'
    path.write_text(json.dumps(dict(report_date=report, usable_from='2026-09-04', cot_index=10,
                                   gate_on=True, threshold=20, updated_at=NOW.isoformat())))
    return path


def test_refresh_timestamp_cannot_renew_74_day_old_cot(tmp_path):
    assert read_gate(gate_file(tmp_path, '2026-06-23'), now=NOW) is None


def test_cot_release_is_bounded_by_signal_asof(tmp_path):
    path = gate_file(tmp_path)
    release = publication_at('2026-09-01')
    assert release == datetime(2026, 9, 4, 15, 30, tzinfo=NY)
    assert read_gate(path, now=NOW, asof=release-timedelta(microseconds=1)) is None
    assert read_gate(path, now=NOW, asof=release) is not None
    assert publication_at('2026-06-30').date().isoformat() == '2026-07-06'
    assert publication_at('2026-11-10').date().isoformat() == '2026-11-16'


@pytest.mark.parametrize('day,close_hour', [('2026-09-04', 16), ('2026-11-27', 13), ('2026-12-24', 13), ('2026-03-06', 16), ('2026-03-09', 16)])
def test_nvda_signal_precedes_regular_or_early_close(day, close_hour):
    date = datetime.fromisoformat(day).date()
    start, end = session_bounds(date)
    assert end.hour == close_hour
    bar = {'ts': int((end-timedelta(minutes=20)).timestamp()*1000), 'open':100,'high':101,'low':99,'close':100,'volume':1}
    brain = OpeningRangeEngine(); brain.init({})
    signal = brain.on_candle(bar, StrategyContext(candles=[bar], symbol='NVDA', timeframe='5m'))
    assert signal.side == Side.EXIT
    assert bar['ts']+300000 == int((end-timedelta(minutes=15)).timestamp()*1000)


def test_nvda_weekend_holiday_and_unknown_calendar():
    assert session_bounds(datetime(2026, 9, 5).date()) is None
    assert session_bounds(datetime(2026, 9, 7).date()) is None
    with pytest.raises(ValueError, match='unreviewed'):
        session_bounds(datetime(2027, 1, 4).date())


def test_net_trade_fee_reconciliation_and_nvda_bounds(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, 'STATE_DIR', tmp_path)
    fills = [dict(action='open', strategy_id='nvda', ts='2026-09-04T14:00:00Z', fee_usd=5),
             dict(action='close', strategy_id='nvda', ts='2026-09-04T15:00:00Z', entry_px=100,price=100.06,qty=100,realized_pnl_usd=.997)]
    (tmp_path/'paper_fills.jsonl').write_text('\n'.join(json.dumps(f) for f in fills))
    assert dashboard._paper_closed_trades()[0]['net_pnl_usd'] == pytest.approx(-4.003)
    (tmp_path/'portfolio.yaml').write_text('strategies:\n  - id: nvda\n    symbol: NVDA\n    risk_pct: 0.0025\n')
    assert dashboard.set_strategy_risk('nvda', .0025)['risk_pct'] == .0025
    assert dashboard._portfolio_config()['strategies'][0]['risk_bounds']['min'] == .0025
    with pytest.raises(ValueError): dashboard.set_strategy_risk('nvda', .0024)


def test_failed_cycles_are_not_healthy_despite_fresh_equity(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, 'STATE_DIR', tmp_path)
    now = datetime.now(UTC).isoformat()
    (tmp_path/'paper_equity.jsonl').write_text(json.dumps({'ts':now,'equity_usd':10000}))
    (tmp_path/'paper_runtime_status.json').write_text(json.dumps({'ts':now,'status':'degraded','errors':{'all':'provider failed'}}))
    worker = dashboard._paper_worker()
    assert not worker['stale'] and not worker['healthy'] and worker['status'] == 'degraded'


def test_broken_legacy_json_preserves_active_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, 'STATE_DIR', tmp_path)
    for name, value in {'_market_signals':{}, '_markets':[], '_binance_15m_candles':[], '_price_series':[], '_shadow_terminal':{}, '_portfolio_shadow':{}, '_llm_fade_shadow':{}}.items():
        monkeypatch.setattr(dashboard, name, lambda *a, _value=value, **kw: _value)
    (tmp_path/'heartbeat.json').write_text('{broken')
    (tmp_path/'paper_positions.json').write_text(json.dumps({'balance_usd':8500,'positions':{}}))
    (tmp_path/'paper_equity.jsonl').write_text(json.dumps({'ts':NOW.isoformat(),'equity_usd':8500}))
    snapshot = dashboard.build_snapshot()
    assert snapshot['partial']
    assert any(error['source']=='heartbeat.json' for error in snapshot['source_errors'])
    assert snapshot['paper']['balance_usd'] == 8500
    assert snapshot['drawdown'] == pytest.approx(.15)
    assert snapshot['guardrail']['status'] == 'caution'
    assert snapshot['guardrail']['risk_multiplier'] == pytest.approx(.55)


def test_nvda_pending_session_exit_requires_fresh_next_session_mark_even_when_d1_fails(tmp_path):
    from test_audit_execution_regressions import position, seed
    friday = datetime(2026,9,4,15,55,tzinfo=NY)
    next_open = datetime(2026,9,8,9,30,tzinfo=NY)
    rows = [dict(ts=int(friday.timestamp()*1000),open=100,high=101,low=99,close=100,volume=1)]
    sc = dict(id='s',engine='opening_range',symbol='NVDA',timeframe='5m',monitor_timeframe='5m',risk_pct=.0025)
    seed(tmp_path, position(symbol='NVDA', opened_ts=datetime(2026,9,4,10,tzinfo=NY).isoformat(),last_monitor_candle_ts=rows[-1]['ts']))
    bot = engine(tmp_path, rows, [sc], execution_mode='observed_mark')
    def fetch(symbol,timeframe,limit):
        if timeframe=='1d': raise OSError('daily unavailable')
        return rows
    bot._provider=fetch
    assert bot.run_cycle(now=next_open)['fills']==[]
    assert bot._load_account().positions['s'].pending_session_exit
    rows.append(dict(ts=int(next_open.timestamp()*1000),open=103,high=104,low=102,close=103,volume=1))
    result=bot.run_cycle(now=next_open+timedelta(minutes=5))
    assert len(result['fills'])==1 and result['fills'][0]['price']==103
    assert result['fills'][0]['reason']=='opening_range_session_close'
    assert bot.run_cycle(now=next_open+timedelta(minutes=5))['fills']==[]


@pytest.mark.parametrize('cadence', [300,900])
def test_nvda_polling_policy_accounts_for_first_reversal(cadence, tmp_path):
    from test_opening_range_engine import _bar, _context
    first=_bar(9,45,100,101,98,100)
    latest=first if cadence==300 else _bar(9,55,100,101,99.5,100)
    context=_context(latest,prior=[] if cadence==300 else [first,_bar(9,50,100,101,99.5,100)])
    sc=dict(id='s',engine='opening_range',symbol='NVDA',timeframe='5m',monitor_timeframe='5m',risk_pct=.0025)
    warmup=[{**first,'ts':first['ts']-86400000+i*300000} for i in range(20)]
    context.candles[:0]=warmup
    bot=engine(tmp_path, context.candles,[sc],execution_mode='observed_mark')
    bot._provider=lambda symbol,tf,limit: context.candles if tf=='5m' else context.candles_by_timeframe['1d']
    now=datetime.fromtimestamp((latest['ts']+300000)/1000,UTC)
    result=bot.run_cycle(now=now)
    if cadence==300:
        assert [fill['action'] for fill in result['fills']]==['open'], result
    else:
        assert result['fills']==[] and result['intents']['s']=='missed_m5_signal'
        assert result['auction'][0]['signal_candle_ts']==first['ts']
        assert bot.run_cycle(now=now)['auction']==[]


def test_reprice_gap_stop_releases_position_id_before_replacement():
    t0=int(NOW.timestamp()*1000)
    provider=SnapshotProvider()
    provider.series[('BTC/USDT','15m')]=[
        dict(ts=t0,open=100,high=101,low=99,close=100),
        dict(ts=t0+STEP,open=85,high=86,low=84,close=85),
        dict(ts=t0+2*STEP,open=86,high=87,low=85,close=86)]
    first=dict(sid='s',position_id='s',side='long',open_ts=t0,legacy_entry=100,stop=90,tp=None,atr_risk=10,risk_distance=10,risk_pct=.01,close_ts=t0+STEP,close_reason='stop_loss',legacy_exit=90)
    second={**first,'open_ts':t0+STEP,'stop':80,'close_ts':t0+2*STEP,'close_reason':'signal'}
    result=reprice([first,second],provider,symbol='BTC/USDT',slippage_bps=0,max_leverage=None)
    assert result['n_trades']==2 and result['skipped']['position_still_open']==0


def test_cot_refresh_fetches_only_current_year_and_preserves_gate_on_error(tmp_path,monkeypatch):
    import csv
    from scripts import update_cot_gate as updater
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return NOW
    monkeypatch.setattr(updater,'datetime',Clock)
    monkeypatch.setattr(updater,'CACHE_DIR',str(tmp_path))
    monkeypatch.setattr(updater,'COT_GATE_PATH',tmp_path/'cot_gate.json')
    key=updater.MARKET.lower().replace(' ','_').replace('-','')[:24]
    history=[dict(report_date=(datetime(2025,12,30)-timedelta(weeks=i)).date().isoformat(),comm_net=i) for i in range(156)]
    with (tmp_path/f'cot_{key}_2006_2026.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=['report_date','comm_net']);writer.writeheader();writer.writerows(history)
    calls=[]
    def fetch(years,market,cache=True):
        calls.append((list(years),cache))
        return [dict(report_date='2026-09-01',comm_net=100)]
    monkeypatch.setattr(updater,'fetch_cot',fetch)
    assert updater.compute_gate()['report_date']=='2026-09-01'
    assert updater.compute_gate()['report_date']=='2026-09-01'
    assert calls==[([2026],False)]
    cached=tmp_path/'cot_current_year.json'
    cached.write_text(json.dumps(dict(year=2026,fetched_at=NOW.isoformat(),rows=[dict(report_date='2026-06-23',comm_net=1)])))
    updater.COT_GATE_PATH.write_text('preserved gate evidence')
    assert updater.main()==1
    assert updater.COT_GATE_PATH.read_text()=='preserved gate evidence'
    assert json.loads((tmp_path/'cot_refresh_status.json').read_text())['status']=='error'


def test_unreviewed_nvda_calendar_cannot_disable_btc_protection(tmp_path):
    from test_audit_execution_regressions import seed,position
    now=datetime(2027,1,4,15,tzinfo=UTC)
    start=int(now.timestamp()*1000)-20*STEP
    rows=[dict(ts=start+i*STEP,open=100.,high=101.,low=99.,close=100.,volume=1.) for i in range(20)]
    rows[-1].update(open=85.,high=89.,low=80.,close=85.)
    seed(tmp_path,position(last_monitor_candle_ts=rows[-2]['ts']))
    nvda=dict(id='nvda',engine='opening_range',symbol='NVDA',timeframe='5m',monitor_timeframe='5m',risk_pct=.0025)
    bot=engine(tmp_path,rows,[strategy(mode='hold'),nvda],execution_mode='observed_mark')
    result=bot.run_cycle(now=now)
    assert result['fills'][0]['strategy_id']=='s' and result['fills'][0]['price']==85
    assert any(key.startswith('nvda') for key in result['errors'])


def test_flat_nvda_outside_session_does_not_report_missed_exit(tmp_path):
    friday=datetime(2026,9,4,15,55,tzinfo=NY)
    rows=[dict(ts=int(friday.timestamp()*1000),open=100,high=101,low=99,close=100,volume=1)]
    sc=dict(id='s',engine='opening_range',symbol='NVDA',timeframe='5m',monitor_timeframe='5m',risk_pct=.0025)
    bot=engine(tmp_path,rows,[sc],execution_mode='observed_mark')
    result=bot.run_cycle(now=NOW)
    assert result['fills']==[] and result['errors']=={} and result['intents']['s']=='no_trade'
