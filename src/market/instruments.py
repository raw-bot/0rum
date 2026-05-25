"""Instrument contract specifications used by sizing and paper accounting."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class InstrumentSpec:
    """Trading contract metadata for an instrument."""

    instrument: str
    contract_size: Decimal
    price_precision: Decimal
    default_spread_usd: Decimal
    default_slippage_usd: Decimal


INSTRUMENT_SPECS = {
    "XAUUSD": InstrumentSpec(
        instrument="XAUUSD",
        contract_size=Decimal("100"),
        price_precision=Decimal("0.01"),
        default_spread_usd=Decimal("0.30"),
        default_slippage_usd=Decimal("0.10"),
    )
}


def get_instrument_spec(instrument: str) -> InstrumentSpec:
    """Return the contract specification for a supported instrument."""

    key = instrument.upper()
    try:
        return INSTRUMENT_SPECS[key]
    except KeyError as exc:
        raise ValueError(f"Unsupported instrument: {instrument}") from exc
