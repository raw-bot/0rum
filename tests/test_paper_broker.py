import unittest

from orum.portfolio.paper_broker import Account, PaperBroker, Position


class PaperBrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.broker = PaperBroker(fee_rt=0.0)  # fee-free for clean arithmetic; fees tested separately
        self.acc = Account(balance_usd=10_000.0)

    def _open_eth(self, price=2000.0, atr_risk=100.0, risk_pct=0.02, equity=10_000.0):
        return self.broker.open(
            self.acc, strategy_id="eth_donchian", symbol="ETH/USDT",
            price=price, atr_risk=atr_risk, risk_pct=risk_pct, equity_for_sizing=equity,
        )

    def test_open_sizes_by_risk_fraction(self):
        fill = self._open_eth()
        # risk 2% of 10k = $200 lost if price falls one atr_risk (100) -> qty = 200/100 = 2 units
        self.assertEqual(fill["action"], "open")
        self.assertAlmostEqual(fill["qty"], 2.0)
        self.assertAlmostEqual(fill["notional_usd"], 4000.0)
        self.assertIn("eth_donchian", self.acc.positions)

    def test_open_sizes_from_atr_even_when_stop_distance_is_wider(self):
        fill = self.broker.open(
            self.acc,
            strategy_id="ut",
            position_id="ut::t2",
            symbol="BTC/USDT",
            price=100.0,
            atr_risk=10.0,
            risk_distance=20.0,
            risk_pct=0.02,
            equity_for_sizing=10_000.0,
            stop_loss_price=80.0,
        )

        self.assertEqual(fill["strategy_id"], "ut")
        self.assertEqual(fill["position_id"], "ut::t2")
        self.assertEqual(fill["qty"], 20.0)
        self.assertEqual(self.acc.positions["ut::t2"].atr_risk, 10.0)
        self.assertEqual(self.acc.positions["ut::t2"].risk_distance, 20.0)

    def test_account_loads_legacy_position_with_key_and_atr_fallback(self):
        account = Account.from_dict(
            {
                "balance_usd": 10_000.0,
                "positions": {
                    "ut": {
                        "strategy_id": "ut",
                        "symbol": "BTC/USDT",
                        "side": "long",
                        "qty": 1.0,
                        "entry_px": 100.0,
                        "notional_usd": 100.0,
                        "risk_pct": 0.01,
                        "atr_risk": 7.0,
                    }
                },
            },
            starting_balance=10_000.0,
        )

        self.assertEqual(account.positions["ut"].position_id, "ut")
        self.assertEqual(account.positions["ut"].risk_distance, 7.0)

    def test_account_loads_legacy_position_without_side_as_long(self):
        account = Account.from_dict(
            {
                "balance_usd": 10_000.0,
                "positions": {
                    "legacy": {
                        "strategy_id": "legacy",
                        "symbol": "BTC/USDT",
                        "qty": 1.0,
                        "entry_px": 100.0,
                        "notional_usd": 100.0,
                        "risk_pct": 0.01,
                        "atr_risk": 7.0,
                    }
                },
            },
            starting_balance=10_000.0,
        )

        self.assertEqual(account.positions["legacy"].side, "long")

    def test_account_rejects_unknown_persisted_side(self):
        with self.assertRaisesRegex(ValueError, "unknown position side"):
            Account.from_dict(
                {
                    "balance_usd": 10_000.0,
                    "positions": {
                        "bad": {
                            "strategy_id": "bad",
                            "symbol": "BTC/USDT",
                            "side": "sell",
                            "qty": 1.0,
                            "entry_px": 100.0,
                            "notional_usd": 100.0,
                            "risk_pct": 0.01,
                            "atr_risk": 7.0,
                        }
                    },
                },
                starting_balance=10_000.0,
            )

    def test_account_rejects_mixed_persisted_sides_for_one_strategy(self):
        base = {
            "strategy_id": "ak", "symbol": "BTC/USDT", "qty": 1.0,
            "entry_px": 100.0, "notional_usd": 100.0,
            "risk_pct": 0.01, "atr_risk": 7.0,
        }
        with self.assertRaisesRegex(ValueError, "mixed position sides"):
            Account.from_dict(
                {
                    "balance_usd": 10_000.0,
                    "positions": {
                        "ak": {**base, "side": "long"},
                        "ak::t2": {
                            **base, "side": "short",
                            "stop_loss_price": 110.0,
                            "take_profit_price": 80.0,
                        },
                    },
                },
                starting_balance=10_000.0,
            )

    def test_account_rejects_malformed_persisted_short_bracket(self):
        with self.assertRaisesRegex(ValueError, "invalid short bracket"):
            Account.from_dict(
                {
                    "balance_usd": 10_000.0,
                    "positions": {"ak": {
                        "strategy_id": "ak", "symbol": "BTC/USDT",
                        "side": "short", "qty": 1.0, "entry_px": 100.0,
                        "notional_usd": 100.0, "risk_pct": 0.01,
                        "atr_risk": 7.0, "stop_loss_price": 90.0,
                        "take_profit_price": 110.0,
                    }},
                },
                starting_balance=10_000.0,
            )

    def test_double_long_is_noop(self):
        self._open_eth()
        self.assertIsNone(self._open_eth())  # already holding -> no re-entry

    def test_exit_while_flat_is_noop(self):
        self.assertIsNone(self.broker.close(self.acc, strategy_id="eth_donchian", price=2100.0))

    def test_close_in_profit_credits_balance(self):
        self._open_eth(price=2000.0, atr_risk=100.0)  # qty = 2
        fill = self.broker.close(self.acc, strategy_id="eth_donchian", price=2100.0)
        self.assertEqual(fill["action"], "close")
        self.assertAlmostEqual(fill["realized_pnl_usd"], 200.0)  # 2 units * +100
        self.assertAlmostEqual(fill["r"], 1.0)                    # +100 move / 100 atr_risk
        self.assertAlmostEqual(self.acc.balance_usd, 10_200.0)
        self.assertNotIn("eth_donchian", self.acc.positions)

    def test_close_in_loss_debits_balance(self):
        self._open_eth(price=2000.0, atr_risk=100.0)  # qty = 2
        self.broker.close(self.acc, strategy_id="eth_donchian", price=1950.0)
        self.assertAlmostEqual(self.acc.balance_usd, 9_900.0)  # 2 units * -50

    def test_short_profit_and_unrealized_pnl_are_direction_aware(self):
        fill = self.broker.open(
            self.acc,
            strategy_id="ak",
            symbol="BTC/USDT",
            side="short",
            price=100.0,
            atr_risk=10.0,
            risk_pct=0.02,
            equity_for_sizing=10_000.0,
            stop_loss_price=110.0,
            take_profit_price=80.0,
        )

        self.assertEqual(fill["side"], "short")
        self.assertAlmostEqual(self.acc.equity({"BTC/USDT": 90.0}), 10_200.0)
        close = self.broker.close(self.acc, strategy_id="ak", price=90.0)
        self.assertEqual(close["side"], "short")
        self.assertAlmostEqual(close["realized_pnl_usd"], 200.0)
        self.assertAlmostEqual(close["r"], 1.0)

    def test_short_requires_valid_stop_before_account_mutation(self):
        for stop in (None, 90.0):
            with self.subTest(stop=stop):
                account = Account(balance_usd=10_000.0)
                fill = self.broker.open(
                    account,
                    strategy_id="ak",
                    symbol="BTC/USDT",
                    side="short",
                    price=100.0,
                    atr_risk=10.0,
                    risk_pct=0.02,
                    equity_for_sizing=10_000.0,
                    stop_loss_price=stop,
                    take_profit_price=80.0,
                )
                self.assertIsNone(fill)
                self.assertEqual(account.balance_usd, 10_000.0)
                self.assertEqual(account.positions, {})

    def test_short_requires_complete_reloadable_bracket(self):
        for target in (None, 0.0, -1.0):
            with self.subTest(target=target):
                account = Account(balance_usd=10_000.0)
                fill = self.broker.open(
                    account,
                    strategy_id="ak",
                    symbol="BTC/USDT",
                    side="short",
                    price=100.0,
                    atr_risk=10.0,
                    risk_pct=0.02,
                    equity_for_sizing=10_000.0,
                    stop_loss_price=110.0,
                    take_profit_price=target,
                )
                self.assertIsNone(fill)
                self.assertEqual(account.positions, {})

    def test_nonfinite_money_inputs_fail_before_mutation(self):
        for override in (
            {"price": float("inf")},
            {"atr_risk": float("inf")},
            {"risk_pct": float("inf")},
            {"equity_for_sizing": float("inf")},
        ):
            with self.subTest(override=override):
                account = Account(balance_usd=10_000.0)
                kwargs = {
                    "strategy_id": "ak", "symbol": "BTC/USDT", "side": "short",
                    "price": 100.0, "atr_risk": 10.0, "risk_pct": 0.02,
                    "equity_for_sizing": 10_000.0, "stop_loss_price": 110.0,
                    "take_profit_price": 80.0,
                }
                kwargs.update(override)
                self.assertIsNone(self.broker.open(account, **kwargs))
                self.assertEqual(account.balance_usd, 10_000.0)
                self.assertEqual(account.positions, {})

    def test_nonfinite_close_price_keeps_short_open(self):
        self.broker.open(
            self.acc, strategy_id="ak", symbol="BTC/USDT", side="short",
            price=100.0, atr_risk=10.0, risk_pct=0.02,
            equity_for_sizing=10_000.0, stop_loss_price=110.0,
            take_profit_price=80.0,
        )
        balance = self.acc.balance_usd

        self.assertIsNone(
            self.broker.close(self.acc, strategy_id="ak", price=float("nan"))
        )
        self.assertEqual(self.acc.balance_usd, balance)
        self.assertIn("ak", self.acc.positions)

    def test_short_rejects_nonfinite_levels_and_long_only_dynamic_state(self):
        for overrides in (
            {"take_profit_price": float("nan")},
            {"dynamic_exit": {"policy": {"mode": "execute"}}},
        ):
            with self.subTest(overrides=overrides):
                account = Account(balance_usd=10_000.0)
                kwargs = {
                    "strategy_id": "ak", "symbol": "BTC/USDT", "side": "short",
                    "price": 100.0, "atr_risk": 10.0, "risk_pct": 0.02,
                    "equity_for_sizing": 10_000.0, "stop_loss_price": 110.0,
                    "take_profit_price": 80.0,
                }
                kwargs.update(overrides)
                fill = self.broker.open(account, **kwargs)
                self.assertIsNone(fill)
                self.assertEqual(account.positions, {})

    def test_broker_rejects_mixed_sides_for_one_strategy(self):
        self.broker.open(
            self.acc,
            strategy_id="ak",
            symbol="BTC/USDT",
            side="long",
            price=100.0,
            atr_risk=10.0,
            risk_pct=0.02,
            equity_for_sizing=10_000.0,
        )

        fill = self.broker.open(
            self.acc,
            strategy_id="ak",
            position_id="ak::t2",
            symbol="BTC/USDT",
            side="short",
            price=100.0,
            atr_risk=10.0,
            risk_pct=0.02,
            equity_for_sizing=10_000.0,
            stop_loss_price=110.0,
            take_profit_price=80.0,
        )

        self.assertIsNone(fill)
        self.assertEqual(set(self.acc.positions), {"ak"})

    def test_equity_marks_open_positions(self):
        self._open_eth(price=2000.0, atr_risk=100.0)  # qty = 2
        self.assertAlmostEqual(self.acc.equity({"ETH/USDT": 2050.0}), 10_100.0)  # +50 * 2
        self.assertAlmostEqual(self.acc.equity({}), 10_000.0)  # no price -> held at cost

    def test_no_open_when_atr_risk_nonpositive(self):
        self.assertIsNone(self._open_eth(atr_risk=0.0))
        self.assertEqual(self.acc.balance_usd, 10_000.0)

    def test_two_strategies_share_one_account_independently(self):
        self._open_eth(price=2000.0, atr_risk=100.0)  # eth qty 2
        self.broker.open(
            self.acc, strategy_id="btc_ak_macd", symbol="BTC/USDT",
            price=60_000.0, atr_risk=3_000.0, risk_pct=0.02, equity_for_sizing=10_000.0,
        )  # btc: risk 200 / 3000 = 0.0667 units
        self.assertEqual(set(self.acc.positions), {"eth_donchian", "btc_ak_macd"})
        # closing one leaves the other untouched
        self.broker.close(self.acc, strategy_id="eth_donchian", price=2100.0)
        self.assertEqual(set(self.acc.positions), {"btc_ak_macd"})

    def test_fees_reduce_pnl(self):
        broker = PaperBroker(fee_rt=0.001)
        acc = Account(balance_usd=10_000.0)
        broker.open(acc, strategy_id="eth", symbol="ETH/USDT", price=2000.0,
                    atr_risk=100.0, risk_pct=0.02, equity_for_sizing=10_000.0)  # qty 2, notional 4000
        # entry fee = 4000 * 0.001/2 = 2.0
        self.assertAlmostEqual(acc.balance_usd, 9_998.0)
        broker.close(acc, strategy_id="eth", price=2000.0)  # flat price
        # exit fee = 2*2000 * 0.001/2 = 2.0 ; realized = 0 - 2 = -2
        self.assertAlmostEqual(acc.balance_usd, 9_996.0)

    def test_account_roundtrips_through_dict(self):
        self._open_eth()
        restored = Account.from_dict(self.acc.to_dict(), starting_balance=10_000.0)
        self.assertEqual(restored.balance_usd, self.acc.balance_usd)
        self.assertIn("eth_donchian", restored.positions)
        self.assertIsInstance(restored.positions["eth_donchian"], Position)

    def test_disabled_positions_keep_legacy_serialized_shape(self):
        self._open_eth()

        payload = self.acc.to_dict()

        self.assertNotIn("dynamic_exit", payload["positions"]["eth_donchian"])
        self.assertNotIn("pending_fills", payload)

    def test_dynamic_state_and_pending_outbox_roundtrip(self):
        self.broker.open(
            self.acc,
            strategy_id="ak",
            symbol="BTC/USDT",
            price=100.0,
            atr_risk=10.0,
            risk_pct=0.02,
            equity_for_sizing=10_000.0,
            dynamic_exit={"policy": {"mode": "observe"}, "mfe_r": 0.0},
        )
        self.acc.pending_fills.append({"fill_id": "one", "action": "close"})

        restored = Account.from_dict(
            self.acc.to_dict(), starting_balance=10_000.0
        )

        self.assertEqual(restored.positions["ak"].dynamic_exit["mfe_r"], 0.0)
        self.assertEqual(restored.pending_fills[0]["fill_id"], "one")

    def test_exit_policy_and_levels_roundtrip_and_are_audited_on_fills(self):
        fill = self.broker.open(
            self.acc,
            strategy_id="btc_ak_macd_4h",
            symbol="BTC/USDT",
            price=100.0,
            atr_risk=10.0,
            risk_pct=0.02,
            equity_for_sizing=10_000.0,
            exit_policy="structural_bracket",
            monitor_timeframe="15m",
            stop_loss_price=90.0,
            take_profit_price=115.0,
            sl_basis="baseline",
            reward_risk_ratio=1.5,
        )

        self.assertEqual(fill["exit_policy"], "structural_bracket")
        self.assertEqual(fill["stop_loss_price"], 90.0)
        self.assertEqual(fill["take_profit_price"], 115.0)
        restored = Account.from_dict(self.acc.to_dict(), starting_balance=10_000.0)
        position = restored.positions["btc_ak_macd_4h"]
        self.assertEqual(position.monitor_timeframe, "15m")
        self.assertEqual(position.sl_basis, "baseline")
        self.assertEqual(position.reward_risk_ratio, 1.5)

        close = self.broker.close(
            restored,
            strategy_id="btc_ak_macd_4h",
            price=115.0,
            reason="take_profit",
        )
        self.assertEqual(close["exit_policy"], "structural_bracket")
        self.assertEqual(close["stop_loss_price"], 90.0)
        self.assertEqual(close["take_profit_price"], 115.0)

    def test_legacy_position_defaults_missing_exit_metadata_safely(self):
        restored = Account.from_dict(
            {
                "balance_usd": 10_000.0,
                "positions": {
                    "legacy": {
                        "strategy_id": "legacy",
                        "symbol": "BTC/USDT",
                        "side": "long",
                        "qty": 1.0,
                        "entry_px": 100.0,
                        "notional_usd": 100.0,
                        "risk_pct": 0.02,
                        "atr_risk": 10.0,
                    },
                },
            },
            starting_balance=10_000.0,
        )

        position = restored.positions["legacy"]
        self.assertEqual(position.exit_policy, "strategy_signal")
        self.assertIsNone(position.stop_loss_price)
        self.assertIsNone(position.take_profit_price)

    def test_account_rejects_malformed_persisted_short_money_fields(self):
        with self.assertRaisesRegex(ValueError, "invalid atr_risk"):
            Account.from_dict(
                {
                    "balance_usd": 10_000.0,
                    "positions": {
                        "ak": {
                            "strategy_id": "ak", "symbol": "BTC/USDT",
                            "side": "short", "qty": 1.0, "entry_px": 100.0,
                            "notional_usd": 100.0, "risk_pct": 0.02,
                            "atr_risk": -10.0, "risk_distance": 10.0,
                            "stop_loss_price": 110.0,
                            "take_profit_price": 80.0,
                        },
                    },
                },
                starting_balance=10_000.0,
            )

    def test_from_dict_defaults_to_starting_balance(self):
        acc = Account.from_dict(None, starting_balance=5_000.0)
        self.assertEqual(acc.balance_usd, 5_000.0)
        self.assertEqual(acc.positions, {})


if __name__ == "__main__":
    unittest.main()
