import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from orum import dashboard
from orum.fsio import atomic_write_json
from orum.portfolio import strategy_accounts as lab
from orum.portfolio.paper_engine import PaperEngine

NOW = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)


def config():
    return {
        "starting_balance_usd": 10000, "candles_limit": 30,
        "execution_mode": "observed_mark", "reentry_policy": "hold",
        "allowed_entry_sides": ["long"], "max_open_positions_by_symbol": {"BTC/USDT": 1},
        "max_symbol_notional_pct": {"BTC/USDT": 1.0},
        "max_total_stop_risk_pct": .05, "max_symbol_stop_risk_pct": .03,
        "entry_drawdown_risk_scale": {"start_pct": .1, "halt_pct": .2, "floor_multiplier": .1},
        "merit_order": ["second", "first"],
        "strategies": [dict(id=s, engine="donchian", symbol="BTC/USDT", timeframe="1d",
                            risk_pct=.01, params={"entry_n": 20, "exit_n": 10}) for s in ("first", "second")],
    }


def candles():
    closes = [100.] * 29 + [110.]
    return [dict(ts=int(NOW.timestamp() * 1000) - (30 - i) * 86400000,
                 open=c, high=c + 1, low=c - 1, close=c, volume=1.) for i, c in enumerate(closes)]


class StrategyAccountsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "experiment"
        self.cot = Path(self.tmp.name) / "cot.json"
        self.cot.write_text('{}')
        self.cfg = config()
        lab.initialize(self.root, self.cfg, "code1", Path(self.tmp.name))
        self.calls = []

    def provider(self, *key):
        self.calls.append(key)
        return candles()

    def cycle(self, **kwargs):
        return lab.run_cycle(self.root, self.provider, self.cot, "code1", now=NOW, **kwargs)

    def test_two_btc_strategies_open_independently_shared_cap_one_and_same_inputs(self):
        original = copy.deepcopy(self.cfg)
        report = self.cycle()
        self.assertEqual(report['status'], 'ok')
        rows = {r['id']: r for r in report['accounts']}
        self.assertEqual(len(rows['first']['open_positions']), 1)
        self.assertEqual(len(rows['second']['open_positions']), 1)
        self.assertEqual(rows[lab.REFERENCE]['open_positions'], ['second'])
        self.assertEqual(self.calls, [('BTC/USDT', '1d', 30)])
        self.assertEqual(self.cfg, original)
        for row in rows.values():
            self.assertLess(row['equity_usd'], 10000)  # entry fee is real in the simulation
            self.assertGreater(row['fees_usd'], 0)
            self.assertEqual(row['closed_trades'], 0)
        self.assertFalse((Path(self.tmp.name) / 'paper_positions.json').exists())
        self.assertTrue((self.root / 'cycles' / report['cycle_id'] / 'inputs.json').exists())

    def test_config_frozen_and_dd_nominal_on_reference_and_individuals(self):
        manifest = json.loads((self.root / 'manifest.json').read_text())
        for account_id, cfg in manifest['configs'].items():
            self.assertNotIn('entry_drawdown_risk_scale', cfg)
            engine = lab.build_account(self.root, account_id, cfg, self.provider, self.cot)
            self.assertEqual(engine._entry_drawdown_multiplier(.6), 1.)
            self.assertTrue(all(str(p).startswith(str(self.root / 'accounts' / account_id)) for p in
                                (engine._positions_path, engine._fills_path, engine._equity_path,
                                 engine._dynamic_exit_path, engine._shadow_regime_path, engine._dynamic_risk_shadow_path)))
        changed = config()
        changed['strategies'][0]['risk_pct'] = .9
        self.assertEqual(lab.initialize(self.root, changed, 'new', Path(self.tmp.name)), manifest)

    def test_cycle_restart_no_duplicate_fills(self):
        self.cycle()
        before = {p: p.read_bytes() for p in self.root.glob('accounts/*/paper_fills.jsonl')}
        lab.run_cycle(self.root, self.provider, self.cot, 'code1', now=NOW + timedelta(minutes=5))
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_crash_between_accounts_resumes_persisted_inputs_without_refetch(self):
        actual = lab.atomic_write_json
        def crash(path, value):
            if path.name == 'second.json':
                raise KeyboardInterrupt('process killed after account commit')
            return actual(path, value)
        with patch.object(lab, 'atomic_write_json', side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                self.cycle()
        fills_before = {p: p.read_bytes() for p in self.root.glob('accounts/*/paper_fills.jsonl')}
        def must_not_fetch(*args):
            raise AssertionError('resumed cycle fetched again')
        result = lab.run_cycle(self.root, must_not_fetch, self.cot, 'code1', now=NOW + timedelta(hours=1))
        self.assertEqual(result['ts'], NOW.isoformat())
        self.assertEqual(result['status'], 'ok')
        self.assertFalse((self.root / 'pending.json').exists())
        for p, payload in fills_before.items():
            self.assertEqual(p.read_bytes(), payload)

    def test_fill_outbox_failure_does_not_stop_others_and_recovers_once(self):
        original = PaperEngine._append_durable
        def fail_first(path, record):
            if path.parent.name == 'first' and path.name == 'paper_fills.jsonl':
                raise OSError('disk interruption')
            return original(path, record)
        with patch.object(PaperEngine, '_append_durable', side_effect=fail_first):
            result = self.cycle()
        rows = {r['id']: r for r in result['accounts']}
        self.assertEqual(rows['first']['status'], 'error')
        self.assertEqual(rows['second']['status'], 'ok')
        result = lab.run_cycle(self.root, self.provider, self.cot, 'code1', now=NOW + timedelta(minutes=5))
        fills = lab._records(self.root / 'accounts' / 'first' / 'paper_fills.jsonl')
        self.assertEqual(len(fills), 1)
        self.assertEqual(result['status'], 'ok')

    def test_snapshot_excludes_unclosed_candles_and_provider_mutations(self):
        current = {**candles()[-1], 'ts': int(NOW.timestamp()*1000)}
        frozen = lab.freeze_inputs([('BTC/USDT', '1d', 30)], lambda *args: candles() + [current], NOW, self.cot)
        provide = lab.frozen_provider(frozen)
        first = provide('BTC/USDT', '1d', 30)
        self.assertEqual(len(first), 30)
        first[-1]['close'] = -999
        self.assertEqual(provide('BTC/USDT', '1d', 30)[-1]['close'], 110.)

    def test_provider_error_shared_and_method_change_is_visible(self):
        def failed(*args):
            self.calls.append(args)
            raise ValueError('missing data')
        result = lab.run_cycle(self.root, failed, self.cot, 'new_code', now=NOW)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result['status'], 'degraded')
        self.assertTrue(result['method_changed'])
        self.assertTrue(all(r['errors'] for r in result['accounts']))

    def test_invalid_ids_and_tampered_config_rejected(self):
        cfg = config(); cfg['strategies'][0]['id'] = '../paper'
        with self.assertRaises(ValueError):
            lab.initialize(self.root / 'other', cfg, 'hash', Path(self.tmp.name))
        manifest = json.loads((self.root / 'manifest.json').read_text())
        manifest['configs']['first']['strategies'][0]['risk_pct'] = .8
        atomic_write_json(self.root / 'manifest.json', manifest)
        with self.assertRaisesRegex(ValueError, 'modified'):
            self.cycle()

    def test_common_eth_marks_do_not_change_native_execution_prices(self):
        cfg = config()
        cfg['strategies'][0]['monitor_timeframe'] = '15m'
        cfg['strategies'][1]['monitor_timeframe'] = '1h'
        for strategy in cfg['strategies']:
            strategy['symbol'] = 'ETH/USDT'
        cfg['max_open_positions_by_symbol'] = {'ETH/USDT': 1}
        root = self.root / 'eth'
        lab.initialize(root, cfg, 'code1', Path(self.tmp.name))
        now = NOW + timedelta(minutes=45)
        def provider(symbol, timeframe, limit):
            if timeframe == '1d':
                return candles()
            interval = 900000 if timeframe == '15m' else 3600000
            cutoff = int(now.timestamp()*1000) // interval * interval
            price = 115. if timeframe == '15m' else 100.
            return [dict(ts=cutoff - interval * (30-i), open=price, high=price+1,
                         low=price-1, close=price, volume=1.) for i in range(30)]
        result = lab.run_cycle(root, provider, self.cot, 'code1', now=now)
        rows = {r['id']: r for r in result['accounts']}
        self.assertTrue(all(r['marks']['ETH/USDT'] == 115. for r in rows.values()))
        self.assertEqual(rows['second']['equity_usd'], rows[lab.REFERENCE]['equity_usd'])
        fill = lab._records(root / 'accounts/shared_reference/paper_fills.jsonl')[0]
        self.assertEqual(fill['price'], 100.)

    def test_cot_reference_clock_stays_at_snapshot_time(self):
        from orum.strategies.base import StrategyContext, Side
        from orum.strategies.cot_gate import read_gate
        old_now = datetime(2026, 8, 1, tzinfo=timezone.utc)
        atomic_write_json(self.cot, {'report_date': '2026-07-28', 'updated_at': old_now.isoformat(),
                                    'gate_on': True, 'cot_index': 12., 'threshold': 20.})
        cfg = {**config(), 'research_fee_roundtrip': .001,
               'strategies': [dict(id='gold', engine='gold_cot', symbol='PAXG/USDT', timeframe='1d', risk_pct=.01)],
               'merit_order': []}
        engine = lab.build_account(self.root, 'gold', cfg, self.provider, self.cot, now=old_now.isoformat())
        candle = {**candles()[-1], 'ts': int(old_now.timestamp()*1000) - 86400000}
        context = StrategyContext(candles=[candle], symbol='PAXG/USDT', timeframe='1d', candles_by_timeframe={'1d':[candle]})
        self.assertIsNone(read_gate(self.cot, now=NOW, asof=old_now))  # stale on the later wall clock
        self.assertEqual(engine._engines['gold'].on_candle(candle, context).side, Side.LONG)

    def test_method_change_remains_visible_after_code_rollback(self):
        lab.run_cycle(self.root, self.provider, self.cot, 'new_code', now=NOW)
        result = lab.run_cycle(self.root, self.provider, self.cot, 'code1', now=NOW + timedelta(minutes=5))
        self.assertTrue(result['method_changed'])

    def test_missing_checkpoint_never_resets_account_with_history(self):
        self.cycle()
        checkpoint = self.root / 'accounts/first/paper_positions.json'
        checkpoint.unlink()
        result = lab.run_cycle(self.root, self.provider, self.cot, 'code1', now=NOW + timedelta(minutes=5))
        row = next(r for r in result['accounts'] if r['id'] == 'first')
        self.assertEqual(row['status'], 'error')
        self.assertIn('refusing balance reset', row['fatal_error'])
        self.assertFalse(checkpoint.exists())

    def test_reference_checkpoint_loss_does_not_stop_independent_accounts(self):
        self.cycle()
        checkpoint = self.root / 'accounts/shared_reference/paper_positions.json'
        checkpoint.unlink()
        result = lab.run_cycle(self.root, self.provider, self.cot, 'code1', now=NOW + timedelta(minutes=5))
        rows = {r['id']: r for r in result['accounts']}
        self.assertEqual(rows[lab.REFERENCE]['status'], 'error')
        self.assertEqual(rows['first']['status'], 'ok')
        self.assertEqual(rows['second']['status'], 'ok')
        self.assertEqual(rows['first']['ts'], (NOW + timedelta(minutes=5)).isoformat(timespec='seconds'))
        self.assertFalse(checkpoint.exists())

    def test_dashboard_missing_stale_partial_and_corrupt_state(self):
        with patch.object(dashboard, 'STATE_DIR', Path(self.tmp.name)):
            self.assertEqual(dashboard._strategy_accounts_state()['status'], 'not_started')
            path = Path(self.tmp.name) / 'strategy_accounts/v1/summary.json'
            atomic_write_json(path, {'ts': NOW.isoformat(), 'status': 'degraded', 'accounts': [{'id': 'first', 'equity_usd': None}]})
            data = dashboard._strategy_accounts_state()
            self.assertEqual(data['accounts'][0]['equity_usd'], None)
            self.assertIn('stale', data)
            atomic_write_json(path, {'ts': '2000-01-01T00:00:00+00:00', 'accounts': []})
            self.assertTrue(dashboard._strategy_accounts_state()['stale'])
            path.write_text('{')
            self.assertEqual(dashboard._strategy_accounts_state()['status'], 'error')


if __name__ == '__main__':
    unittest.main()
