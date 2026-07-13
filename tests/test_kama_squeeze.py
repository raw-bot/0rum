import math

from orum.strategies.kama_squeeze import (
    KamaSqueezeParams,
    KamaSqueezeState,
    compute_kama_state,
    entry_at,
    linear_regression_endpoint,
    open_long_position,
    replay_strategy,
    squeeze_release_flags,
    update_long_position,
)


def _candles(closes: list[float], *, half_range: float = 1.0) -> list[dict]:
    return [
        {
            "ts": i,
            "open": close,
            "high": close + half_range,
            "low": close - half_range,
            "close": close,
            "volume": 1.0,
        }
        for i, close in enumerate(closes)
    ]


def test_defaults_freeze_supplied_settings_and_research_assumptions():
    params = KamaSqueezeParams()

    assert params.efficiency_length == 32
    assert (params.fast_length, params.slow_length) == (2, 50)
    assert params.slope_lookback == 1
    assert params.minimum_efficiency == 0.20
    assert params.squeeze_length == 16
    assert (params.bb_multiplier, params.kc_multiplier) == (2.0, 1.5)
    assert params.minimum_squeeze_bars == 2
    assert params.atr_length == 14
    assert params.momentum_length == 16
    assert (params.initial_stop_atr, params.trailing_stop_atr) == (2.8, 5.0)


def test_flat_market_efficiency_ratio_is_zero_not_nan():
    state = compute_kama_state(_candles([100.0] * 80))

    assert state.efficiency_ratio[-1] == 0.0
    assert not math.isnan(state.efficiency_ratio[-1])
    assert not math.isnan(state.kama[-1])


def test_kama_rises_on_a_persistent_uptrend():
    state = compute_kama_state(_candles([100.0 + i for i in range(100)]))

    assert state.kama[-1] > state.kama[-2]
    assert state.efficiency_ratio[-1] == 1.0
    assert state.kama[-1] < state.closes[-1]


def test_squeeze_release_requires_the_configured_consecutive_duration():
    assert squeeze_release_flags([False, True, False], minimum_bars=2) == [False, False, False]
    assert squeeze_release_flags([False, True, True, False], minimum_bars=2) == [False, False, False, True]
    assert squeeze_release_flags([True, True, True, False], minimum_bars=3) == [False, False, False, True]


def test_linear_regression_endpoint_matches_the_latest_point_on_a_line():
    assert linear_regression_endpoint([1.0, 2.0, 3.0, 4.0]) == 4.0
    assert linear_regression_endpoint([-4.0, -2.0, 0.0, 2.0]) == 2.0


def test_entry_requires_release_positive_accelerating_momentum_rising_kama_and_er_gate():
    params = KamaSqueezeParams()
    state = KamaSqueezeState(
        closes=[100.0, 105.0, 110.0],
        atr=[1.0, 1.0, 1.0],
        efficiency_ratio=[0.1, 0.19, 0.30],
        kama=[99.0, 103.0, 104.0],
        squeeze_on=[True, True, False],
        squeeze_release=[False, False, True],
        momentum=[-1.0, 1.0, 2.0],
    )

    assert entry_at(state, 2, params)

    state.efficiency_ratio[2] = 0.20
    assert not entry_at(state, 2, params), "ER equality must not pass a strict > 0.20 gate"


def test_initial_stop_uses_fill_price_and_signal_bar_atr():
    position = open_long_position(fill_price=100.0, signal_atr=2.0, entry_index=12)

    assert position.entry_price == 100.0
    assert position.initial_stop == 94.4
    assert position.effective_stop == 94.4
    assert position.highest_close == 100.0


def test_trailing_stop_ratchets_from_highest_close_and_never_moves_backwards():
    position = open_long_position(fill_price=100.0, signal_atr=2.0, entry_index=12)

    position, reason = update_long_position(
        position, close=110.0, atr=2.0, kama=105.0, momentum=1.0,
    )
    assert reason is None
    assert position.effective_stop == 100.0

    position, reason = update_long_position(
        position, close=105.0, atr=4.0, kama=104.0, momentum=0.5,
    )
    assert reason is None
    assert position.effective_stop == 100.0


def test_confirmed_close_below_ratchet_requests_exit_without_using_intrabar_prices():
    position = open_long_position(fill_price=100.0, signal_atr=2.0, entry_index=12)
    position, _ = update_long_position(
        position, close=110.0, atr=2.0, kama=105.0, momentum=1.0,
    )

    position, reason = update_long_position(
        position, close=99.0, atr=4.0, kama=98.0, momentum=0.5,
    )

    assert position.effective_stop == 100.0
    assert reason == "atr_trail_close"


def test_close_below_kama_with_negative_momentum_forces_exit_before_stop():
    position = open_long_position(fill_price=100.0, signal_atr=2.0, entry_index=12)

    position, reason = update_long_position(
        position, close=99.0, atr=2.0, kama=100.0, momentum=-0.1,
    )

    assert position.effective_stop == 94.4
    assert reason == "kama_negative_momentum"


def test_replay_reconstructs_next_bar_entry_and_close_based_exit_without_hidden_state():
    candles = _candles([100.0, 105.0, 110.0, 99.0, 98.0])
    candles[2]["open"] = 106.0
    candles[4]["open"] = 98.5
    state = KamaSqueezeState(
        closes=[100.0, 105.0, 110.0, 99.0, 98.0],
        atr=[2.0] * 5,
        efficiency_ratio=[0.1, 0.30, 0.30, 0.30, 0.30],
        kama=[99.0, 103.0, 104.0, 98.0, 97.0],
        squeeze_on=[True, False, False, False, False],
        squeeze_release=[False, True, False, False, False],
        momentum=[-1.0, 1.0, 2.0, 0.5, 0.4],
    )

    replay = replay_strategy(candles, state=state)

    assert replay.signals[1].value == "long"
    assert replay.entry_fills == [(2, 106.0)]
    assert replay.signals[3].value == "exit"
    assert replay.exit_reasons[3] == "atr_trail_close"
    assert replay.exit_fills == [(4, 98.5)]
    assert replay.position is None
