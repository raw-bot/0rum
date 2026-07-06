"""5 distinct liquidation-continuation strategy definitions.

Each strategy returns (signal_positions, directions) -- integer bar positions
in `feat` where a signal fires (known as of that bar's close) and the trade
direction (+1 continuation-long, -1 continuation-short). Execution is always
at the NEXT bar's open (see backtest.run_backtest).

All five share the same base "proxy liquidation spike" (abnormal volume +
abnormal directional move, calibrated against real BTCUSD_PERP liquidation
tape in calibrate_proxy.py) but differ in which secondary, mechanistically
distinct confirmation each one requires -- this is what makes them 5 separate
angles on the same continuation hypothesis rather than 5 cosmetic variants of
one signal.
"""
import numpy as np


def _base_spike(feat, vol_z_th=2.0, ret_z_th=2.0):
    return (feat["vol_z"] > vol_z_th) & (feat["ret_z"] > ret_z_th)


def strategy1_momentum(feat, vol_z_th=2.0, ret_z_th=2.0, body_ratio_th=0.6):
    """Spike + momentum confirmation: the spike candle must close strongly in
    its own direction (close near the bar's high for up-spikes / near the low
    for down-spikes), i.e. a trend-day close rather than a spike-and-fade
    candle."""
    spike = _base_spike(feat, vol_z_th, ret_z_th)
    rng = (feat["high"] - feat["low"]).replace(0, np.nan)
    body_ratio = np.where(feat["ret"] > 0,
                           (feat["close"] - feat["low"]) / rng,
                           (feat["high"] - feat["close"]) / rng)
    confirm = spike & (body_ratio > body_ratio_th)
    confirm = confirm.fillna(False)
    direction = np.sign(feat["ret"])
    idx = np.where(confirm.to_numpy())[0]
    return idx, direction.to_numpy()[idx].astype(int)


def strategy2_imbalance(feat, taker_z_th=2.5, vol_z_th=1.0):
    """Long/short liquidation imbalance, proxied by REAL taker buy/sell volume
    ratio (sum_taker_long_short_vol_ratio, an actual Binance-computed metric,
    not derived from price candles) reaching an abnormal extreme, gated by
    at least mildly elevated traded volume."""
    cond = (feat["taker_ls_z"].abs() > taker_z_th) & (feat["vol_z"] > vol_z_th)
    cond = cond.fillna(False)
    direction = np.sign(feat["taker_ls_z"])
    idx = np.where(cond.to_numpy())[0]
    return idx, direction.to_numpy()[idx].astype(int)


def strategy3_oi(feat, vol_z_th=2.0, ret_z_th=2.0, oi_z_th=-1.5):
    """Spike + open-interest confirmation: requires concurrent abnormal OI
    CONTRACTION (closing/forced-deleveraging signature) rather than OI
    expansion (fresh positioning), to distinguish a liquidation flush from an
    aggressive new-money breakout."""
    spike = _base_spike(feat, vol_z_th, ret_z_th)
    oi_confirm = feat["oi_chg_z"] < oi_z_th
    cond = (spike & oi_confirm).fillna(False)
    direction = np.sign(feat["ret"])
    idx = np.where(cond.to_numpy())[0]
    return idx, direction.to_numpy()[idx].astype(int)


def strategy4_volatility(feat, vol_z_th=2.5, ret_z_th=1.5, range_z_th=2.0):
    """Spike + volatility/volume expansion: requires true-range (ATR-style)
    expansion on top of the close-to-close move/volume spike -- a regime
    shift in realized volatility, not just one big candle."""
    cond = ((feat["vol_z"] > vol_z_th) & (feat["ret_z"] > ret_z_th) &
            (feat["range_z"] > range_z_th))
    cond = cond.fillna(False)
    direction = np.sign(feat["ret"])
    idx = np.where(cond.to_numpy())[0]
    return idx, direction.to_numpy()[idx].astype(int)


def strategy5_levels(feat, vol_z_th=2.0, ret_z_th=2.0, level_col="hh_8h", level_col_low="ll_8h"):
    """Spike occurring exactly where it breaks a rolling 8h high/low (a
    liquidity zone where resting stops/liquidations cluster) -- i.e. the
    spike candle's high/low actually takes out the prior swing level, a
    breakout-through-liquidity-zone continuation entry."""
    spike = _base_spike(feat, vol_z_th, ret_z_th)
    up_break = (feat["ret"] > 0) & (feat["high"] >= feat[level_col].shift(1))
    down_break = (feat["ret"] < 0) & (feat["low"] <= feat[level_col_low].shift(1))
    cond = (spike & (up_break | down_break)).fillna(False)
    direction = np.sign(feat["ret"])
    idx = np.where(cond.to_numpy())[0]
    return idx, direction.to_numpy()[idx].astype(int)


STRATEGIES = {
    "S1_momentum": strategy1_momentum,
    "S2_imbalance": strategy2_imbalance,
    "S3_oi": strategy3_oi,
    "S4_volatility": strategy4_volatility,
    "S5_levels": strategy5_levels,
}
