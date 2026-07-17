"""Pure ETH/EUR volatility-edge challenger and walk-forward evaluation."""

from __future__ import annotations

import math
import statistics

from scripts.research_eur_universe import _atr, _validate_candles, simulate_donchian

DAY_MS = 86_400_000


def _aligned(eth_candles: list[dict], btc_candles: list[dict]) -> tuple[list[dict], list[dict]]:
    _validate_candles(eth_candles)
    _validate_candles(btc_candles)
    eth_by_ts = {int(candle["ts"]): candle for candle in eth_candles}
    btc_by_ts = {int(candle["ts"]): candle for candle in btc_candles}
    timestamps = sorted(eth_by_ts.keys() & btc_by_ts.keys())
    return [eth_by_ts[ts] for ts in timestamps], [btc_by_ts[ts] for ts in timestamps]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def entry_conditions_at(eth_candles: list[dict], btc_candles: list[dict], index: int) -> dict[str, bool]:
    """Return the four frozen entry predicates at one confirmed daily close."""
    eth, btc = _aligned(eth_candles, btc_candles)
    if index < 105 or index >= len(eth):
        return {
            "breakout_20": False,
            "atr_expanding": False,
            "trend_100": False,
            "relative_strength_50": False,
        }

    closes = [float(candle["close"]) for candle in eth]
    btc_closes = [float(candle["close"]) for candle in btc]
    atr = _atr(eth)
    ratios = [eth_close / btc_close for eth_close, btc_close in zip(closes, btc_closes, strict=True)]
    sma100_now = _mean(closes[index - 99:index + 1])
    sma100_five_ago = _mean(closes[index - 104:index - 4])
    return {
        "breakout_20": closes[index] > max(closes[index - 20:index]),
        "atr_expanding": atr[index] > atr[index - 5],
        "trend_100": closes[index] > sma100_now and sma100_now > sma100_five_ago,
        "relative_strength_50": ratios[index] > _mean(ratios[index - 49:index + 1]),
    }


def simulate_eth_challenger(
    eth_candles: list[dict],
    btc_candles: list[dict],
    *,
    fee_rt: float = 0.001,
    slippage: float = 0.0003,
) -> list[dict]:
    """Simulate one long ETH position with confirmed-close, next-open fills."""
    eth, btc = _aligned(eth_candles, btc_candles)
    closes = [float(candle["close"]) for candle in eth]
    atr = _atr(eth)
    trades: list[dict] = []
    pending_entry: int | None = None
    pending_exit: int | None = None
    position: dict | None = None

    for index, candle in enumerate(eth):
        if pending_exit is not None and position is not None:
            exit_price = float(candle["open"]) * (1.0 - slippage)
            fee_move = fee_rt * (position["entry_price"] + exit_price) / 2.0
            trades.append({
                **position,
                "exit_signal_index": pending_exit,
                "exit_index": index,
                "exit_price": exit_price,
                "r": (exit_price - position["entry_price"] - fee_move) / position["risk_distance"],
            })
            position = None
            pending_exit = None

        if pending_entry is not None:
            risk_distance = 2.0 * atr[pending_entry]
            if risk_distance > 0:
                position = {
                    "signal_index": pending_entry,
                    "entry_index": index,
                    "entry_price": float(candle["open"]) * (1.0 + slippage),
                    "risk_distance": risk_distance,
                }
            pending_entry = None

        if position is not None:
            if index >= 10 and closes[index] < min(closes[index - 10:index]) and index + 1 < len(eth):
                pending_exit = index
        elif index + 1 < len(eth):
            conditions = entry_conditions_at(eth, btc, index)
            if all(conditions.values()):
                pending_entry = index

    return trades


def _metrics(trades: list[dict], *, bars: int) -> dict:
    returns = [float(trade["r"]) for trade in trades]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    cumulative = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in returns:
        cumulative += value
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
    exposure_bars = sum(max(0, int(t["exit_index"]) - int(t["entry_index"])) for t in trades)
    return {
        "trades": len(returns),
        "win_rate": len(wins) / len(returns) if returns else 0.0,
        "profit_factor": sum(wins) / -sum(losses) if losses and sum(losses) < 0 else None,
        "expectancy_r": sum(returns) / len(returns) if returns else 0.0,
        "net_r": sum(returns),
        "max_realized_drawdown_r": drawdown,
        "exposure": min(1.0, exposure_bars / bars) if bars else 0.0,
    }


def walk_forward_report(
    trades: list[dict],
    candles: list[dict],
    *,
    warmup_bars: int = 730,
    test_bars: int = 365,
) -> dict:
    if warmup_bars < 1 or test_bars < 1:
        raise ValueError("walk-forward lengths must be positive")
    windows = []
    aggregate_trades: list[dict] = []
    for start in range(warmup_bars, len(candles) - test_bars + 1, test_bars):
        end = start + test_bars
        selected = [trade for trade in trades if start <= int(trade["signal_index"]) < end]
        metrics = _metrics(selected, bars=test_bars)
        windows.append({"start_index": start, "end_index": end, **metrics})
        aggregate_trades.extend(selected)
    positive = sum(1 for window in windows if float(window["expectancy_r"]) > 0)
    return {
        "windows": windows,
        "aggregate": _metrics(aggregate_trades, bars=len(windows) * test_bars),
        "positive_window_fraction": positive / len(windows) if windows else 0.0,
    }


def return_portability(primary: list[dict], reference: list[dict]) -> dict:
    left, right = _aligned(primary, reference)
    left_close = [float(candle["close"]) for candle in left]
    right_close = [float(candle["close"]) for candle in right]
    left_returns = [math.log(left_close[i] / left_close[i - 1]) for i in range(1, len(left_close))]
    right_returns = [math.log(right_close[i] / right_close[i - 1]) for i in range(1, len(right_close))]
    correlation = math.nan
    if len(left_returns) >= 2 and statistics.pstdev(left_returns) > 0 and statistics.pstdev(right_returns) > 0:
        correlation = statistics.correlation(left_returns, right_returns)
    differences = [abs(a - b) for a, b in zip(left_returns, right_returns, strict=True)]
    median_difference = statistics.median(differences) if differences else math.nan
    passed = (
        not math.isnan(correlation)
        and not math.isnan(median_difference)
        and correlation >= 0.995
        and median_difference <= 0.002
    )
    return {
        "common_bars": len(left),
        "return_observations": len(left_returns),
        "correlation": correlation,
        "median_abs_return_difference": median_difference,
        "passed": passed,
    }


def evaluate_promotion(
    aggregate: dict,
    benchmark: dict,
    positive_window_fraction: float,
    portability: dict[str, dict],
) -> dict:
    gates = {
        "minimum_20_trades": int(aggregate["trades"]) >= 20,
        "profit_factor_above_1_25": aggregate["profit_factor"] is not None
        and float(aggregate["profit_factor"]) > 1.25,
        "expectancy_above_0_15r": float(aggregate["expectancy_r"]) > 0.15,
        "positive_windows_at_least_60pct": positive_window_fraction >= 0.60,
        "drawdown_at_most_8r": float(aggregate["max_realized_drawdown_r"]) <= 8.0,
        "beats_donchian_expectancy": float(aggregate["expectancy_r"]) > float(benchmark["expectancy_r"]),
        "btc_portability": bool(portability.get("BTC/EUR", {}).get("passed")),
        "eth_portability": bool(portability.get("ETH/EUR", {}).get("passed")),
    }
    return {"eligible": all(gates.values()), "gates": gates}


def build_research_report(
    coinbase: dict[str, list[dict]],
    kraken: dict[str, list[dict]],
    *,
    retrieved_at: str,
) -> dict:
    """Compare frozen ETH models and venue portability without activation."""
    eth, btc = _aligned(coinbase["ETH/EUR"], coinbase["BTC/EUR"])
    challenger_trades = simulate_eth_challenger(eth, btc)
    benchmark_trades = simulate_donchian(eth)
    challenger = walk_forward_report(challenger_trades, eth)
    benchmark = walk_forward_report(benchmark_trades, eth)
    portability = {
        symbol: return_portability(coinbase[symbol], kraken[symbol])
        for symbol in ("BTC/EUR", "ETH/EUR")
    }
    promotion = evaluate_promotion(
        challenger["aggregate"],
        benchmark["aggregate"],
        challenger["positive_window_fraction"],
        portability,
    )
    return {
        "retrieved_at": retrieved_at,
        "history": {
            "common_bars": len(eth),
            "first_timestamp": int(eth[0]["ts"]) if eth else None,
            "last_timestamp": int(eth[-1]["ts"]) if eth else None,
        },
        "costs": {"round_trip_fee": 0.001, "slippage_per_fill": 0.0003},
        "portability": portability,
        "benchmark": benchmark,
        "challenger": challenger,
        "promotion": promotion,
    }
