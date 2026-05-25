"""Market regime detector for XAUUSD signal pipeline.

Classifies H1 candle data into TRENDING_UP, TRENDING_DOWN, RANGING, or HIGH_VOL
using ADX(14) + ATR percentile rules defined in CLAUDE.md §11.4.

No DB I/O — returns a MarketRegime Pydantic object. The PipelineRunner (Plan 02)
persists the MarketRegimeORM after calling detect().
"""

from datetime import datetime, timezone

import numpy as np
import structlog

from src.indicators.atr import atr_wilder
from src.indicators.ema import ema
from src.models.signal_data import MarketRegime, MarketRegimeType

log = structlog.get_logger(__name__)


class RegimeDetector:
    """Classify current market regime from H1 candle data.

    Implements the four-regime model from CLAUDE.md §11.4:
    - HIGH_VOL: ATR percentile >= 0.90 (overrides all)
    - TRENDING_UP: ADX > 25 AND EMA(50) > EMA(200)
    - TRENDING_DOWN: ADX > 25 AND EMA(50) < EMA(200)
    - RANGING: default fallback (ADX <= 25)
    """

    async def detect(self, candles: list) -> MarketRegime:
        """Classify the current market regime from H1 candles.

        Args:
            candles: List of Candle ORM objects in oldest→newest order.
                     H1 timeframe expected, at least 200 rows for reliable results.

        Returns:
            MarketRegime Pydantic object with regime, atr_value, atr_pctile, adx_value.
        """
        # 1. Compute inputs
        atr_value = self._calculate_atr(candles)
        atr_pctile = self._calculate_atr_percentile(candles)
        adx_value = self._calculate_adx(candles)

        closes = [float(c.close) for c in candles]
        ema50 = self._calculate_ema(closes, 50)
        ema200 = self._calculate_ema(closes, 200)

        # 2. HIGH_VOL overrides all others
        if atr_pctile >= 0.90:
            regime = MarketRegimeType.HIGH_VOL

        # 3. TRENDING_UP: ADX > 25 AND EMA50 > EMA200
        elif adx_value > 25 and ema50 > ema200:
            regime = MarketRegimeType.TRENDING_UP

        # 4. TRENDING_DOWN: ADX > 25 AND EMA50 < EMA200
        elif adx_value > 25 and ema50 < ema200:
            regime = MarketRegimeType.TRENDING_DOWN

        # 5. Default: RANGING (ADX <= 25 OR ATR < 50th percentile)
        else:
            regime = MarketRegimeType.RANGING

        log.info(
            "regime_detector.detected",
            regime=regime.value,
            atr_value=round(atr_value, 5),
            atr_pctile=round(atr_pctile, 4),
            adx_value=round(adx_value, 4) if adx_value else None,
        )

        return MarketRegime(
            timestamp=datetime.now(timezone.utc),
            regime=regime,
            atr_value=atr_value,
            atr_pctile=atr_pctile,
            adx_value=adx_value,
        )

    def _calculate_atr(self, candles: list, period: int = 14) -> float:
        """Compute ATR using True Range over the last `period` candles.

        True Range = max(high - low, abs(high - prev_close), abs(low - prev_close)).
        ATR = simple mean of the last `period` True Range values.

        Args:
            candles: List of Candle ORM objects (oldest→newest).
            period: Lookback period, default 14.

        Returns:
            ATR value, or 0.0 if fewer than period+1 candles available.
        """
        if len(candles) < period + 1:
            return 0.0

        return atr_wilder(candles, period=period)

    def _calculate_adx(self, candles: list, period: int = 14) -> float:
        """Compute ADX(14) using Wilder's EMA smoothing.

        Algorithm:
        1. Compute +DM, -DM, TR for each candle pair.
        2. Smooth with Wilder's EMA (alpha = 1/period).
        3. ADX = 100 × EMA(abs(+DI - -DI) / (+DI + -DI)) over period bars.

        Args:
            candles: List of Candle ORM objects (oldest→newest).
            period: Lookback period, default 14.

        Returns:
            ADX value in [0, 100], or 0.0 if insufficient data.
        """
        if len(candles) < period * 2:
            return 0.0

        # Compute directional movement and True Range for each bar
        plus_dm_list = []
        minus_dm_list = []
        tr_list = []

        for i in range(1, len(candles)):
            high = float(candles[i].high)
            low = float(candles[i].low)
            prev_high = float(candles[i - 1].high)
            prev_low = float(candles[i - 1].low)
            prev_close = float(candles[i - 1].close)

            # Directional Movement
            up_move = high - prev_high
            down_move = prev_low - low

            plus_dm = up_move if up_move > down_move and up_move > 0 else 0.0
            minus_dm = down_move if down_move > up_move and down_move > 0 else 0.0

            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))

            plus_dm_list.append(plus_dm)
            minus_dm_list.append(minus_dm)
            tr_list.append(tr)

        # Wilder smoothing: alpha = 1/period
        alpha = 1.0 / period

        # Seed with simple sum of first `period` values (Wilder's method)
        smoothed_plus_dm = sum(plus_dm_list[:period])
        smoothed_minus_dm = sum(minus_dm_list[:period])
        smoothed_tr = sum(tr_list[:period])

        dx_list = []

        for i in range(period, len(plus_dm_list)):
            smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / period) + plus_dm_list[i]
            smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dm_list[i]
            smoothed_tr = smoothed_tr - (smoothed_tr / period) + tr_list[i]

            # Directional Indicators
            if smoothed_tr == 0:
                dx_list.append(0.0)
                continue

            plus_di = 100.0 * smoothed_plus_dm / smoothed_tr
            minus_di = 100.0 * smoothed_minus_dm / smoothed_tr

            di_sum = plus_di + minus_di
            if di_sum == 0:
                # Guard against ZeroDivisionError on flat candle data (T-04-02)
                dx_list.append(0.0)
                continue

            dx = 100.0 * abs(plus_di - minus_di) / di_sum
            dx_list.append(dx)

        if not dx_list:
            return 0.0

        # ADX = Wilder EMA of DX over period
        if len(dx_list) < period:
            return float(np.mean(dx_list))

        adx = float(np.mean(dx_list[:period]))
        for dx in dx_list[period:]:
            adx = adx * (1.0 - alpha) + dx * alpha

        return adx

    def _calculate_atr_percentile(
        self, candles: list, period: int = 14, lookback: int = 100
    ) -> float:
        """Compute the percentile rank of the current ATR among historical ATR values.

        Calculates ATR(14) for each of the last `lookback` windows and returns the
        percentile rank (0.0–1.0) of the most recent ATR value.

        Args:
            candles: List of Candle ORM objects (oldest→newest).
            period: ATR period, default 14.
            lookback: Number of historical windows to compare against, default 100.

        Returns:
            Percentile rank in [0.0, 1.0], or 0.5 if insufficient data.
        """
        if len(candles) < period + lookback:
            return 0.5

        historical_atrs = []
        for i in range(lookback):
            # Each window: candles up to position -(lookback - i)
            end_idx = len(candles) - (lookback - i - 1)
            window = candles[: end_idx]
            atr = self._calculate_atr(window, period=period)
            historical_atrs.append(atr)

        current_atr = historical_atrs[-1]
        historical_array = np.array(historical_atrs)

        from scipy.stats import percentileofscore

        pctile = percentileofscore(historical_array, current_atr) / 100.0
        return float(pctile)

    def _calculate_ema(self, values: list[float], period: int) -> float:
        """Compute Exponential Moving Average for the last value in the series.

        Seeds with SMA(period) then applies alpha = 2 / (period + 1).

        Args:
            values: List of float values (oldest→newest).
            period: EMA period.

        Returns:
            Current EMA value, or 0.0 if fewer than `period` values.
        """
        if len(values) < period:
            return 0.0

        ema_values = ema(values, period)
        if not ema_values or np.isnan(ema_values[-1]):
            return 0.0
        return float(ema_values[-1])
