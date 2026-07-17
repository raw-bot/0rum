"""Research the approved EUR spot universe without touching paper state.

Kraken's public OHLC endpoint returns at most 720 recent entries and always
includes the current incomplete candle. ``CcxtClosedCandleProvider`` removes
that tail before this module sees it. Results are consequently provisional and
must never auto-promote a pair into ``state/portfolio.yaml``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import ccxt

from orum.portfolio.ccxt_provider import CcxtClosedCandleProvider

EUR_UNIVERSE = ("BTC/EUR", "ETH/EUR", "SOL/EUR", "XRP/EUR", "AAVE/EUR")


def _validate_candles(candles: list[dict]) -> None:
    previous: int | None = None
    for candle in candles:
        ts = int(candle["ts"])
        if previous is not None and ts <= previous:
            raise ValueError("candle timestamps must be strictly increasing")
        previous = ts
        open_, high, low, close = (float(candle[key]) for key in ("open", "high", "low", "close"))
        if low > high or not (low <= open_ <= high and low <= close <= high):
            raise ValueError(f"invalid OHLC at timestamp {ts}")


def _atr(candles: list[dict], length: int = 14) -> list[float]:
    if not candles:
        return []
    true_ranges = [float(candles[0]["high"]) - float(candles[0]["low"])]
    for index in range(1, len(candles)):
        high = float(candles[index]["high"])
        low = float(candles[index]["low"])
        previous_close = float(candles[index - 1]["close"])
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    alpha = 1.0 / length
    out = [true_ranges[0]]
    for value in true_ranges[1:]:
        out.append(alpha * value + (1.0 - alpha) * out[-1])
    return out


def simulate_donchian(
    candles: list[dict],
    *,
    entry_n: int = 20,
    exit_n: int = 10,
    fee_rt: float = 0.001,
    slippage: float = 0.0003,
) -> list[dict]:
    """One-position long-only Donchian simulation with next-open fills."""
    _validate_candles(candles)
    if entry_n < 1 or exit_n < 1:
        raise ValueError("Donchian lengths must be positive")
    closes = [float(candle["close"]) for candle in candles]
    atr = _atr(candles)
    trades: list[dict] = []
    pending_entry: int | None = None
    pending_exit: int | None = None
    position: dict | None = None

    for index, candle in enumerate(candles):
        if pending_exit is not None and position is not None:
            exit_price = float(candle["open"]) * (1.0 - slippage)
            fee_move = fee_rt * (position["entry_price"] + exit_price) / 2.0
            r_value = (exit_price - position["entry_price"] - fee_move) / position["risk_distance"]
            trades.append({
                **position,
                "exit_signal_index": pending_exit,
                "exit_index": index,
                "exit_price": exit_price,
                "r": r_value,
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
            if index >= exit_n:
                prior_low = min(closes[index - exit_n:index])
                if closes[index] < prior_low and index + 1 < len(candles):
                    pending_exit = index
        elif index >= entry_n:
            prior_high = max(closes[index - entry_n:index])
            if closes[index] > prior_high and index + 1 < len(candles):
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


def chronological_report(
    trades: list[dict], candles: list[dict], *, split_fraction: float = 0.70,
) -> dict:
    if not 0.0 < split_fraction < 1.0:
        raise ValueError("split_fraction must be between zero and one")
    split_index = int(len(candles) * split_fraction)
    train = [trade for trade in trades if int(trade["exit_index"]) < split_index]
    holdout = [trade for trade in trades if int(trade["signal_index"]) >= split_index]
    crossing = [
        trade for trade in trades
        if int(trade["signal_index"]) < split_index <= int(trade["exit_index"])
    ]
    reasons = []
    if len(candles) < 1_000:
        reasons.append("history_below_1000_bars")
    if len(holdout) < 20:
        reasons.append("holdout_below_20_trades")
    return {
        "bars": len(candles),
        "split_fraction": split_fraction,
        "split_index": split_index,
        "full": _metrics(trades, bars=len(candles)),
        "train": _metrics(train, bars=max(1, split_index)),
        "holdout": _metrics(holdout, bars=max(1, len(candles) - split_index)),
        "excluded_boundary_trades": len(crossing),
        "provisional_only": bool(reasons),
        "provisional_reasons": reasons,
    }


def research_universe(provider: CcxtClosedCandleProvider) -> dict:
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    output = {"retrieved_at": retrieved_at, "venue": "kraken", "timeframe": "1d", "pairs": {}}
    for symbol in EUR_UNIVERSE:
        candles = provider("kraken", symbol, "1d", 720)
        trades = simulate_donchian(candles)
        report = chronological_report(trades, candles)
        report["first_candle"] = (
            datetime.fromtimestamp(candles[0]["ts"] / 1000, timezone.utc).isoformat() if candles else None
        )
        report["last_candle"] = (
            datetime.fromtimestamp(candles[-1]["ts"] / 1000, timezone.utc).isoformat() if candles else None
        )
        output["pairs"][symbol] = report
    return output


def main() -> None:
    exchange = ccxt.kraken({"enableRateLimit": True})
    provider = CcxtClosedCandleProvider({"kraken": exchange})
    print(json.dumps(research_universe(provider), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
