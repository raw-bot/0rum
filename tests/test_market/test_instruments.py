from decimal import Decimal

import pytest

from src.market.instruments import get_instrument_spec


def test_xauusd_contract_size_is_named_and_decimal():
    spec = get_instrument_spec("XAUUSD")

    assert spec.instrument == "XAUUSD"
    assert spec.contract_size == Decimal("100")
    assert spec.price_precision == Decimal("0.01")


def test_get_instrument_spec_normalizes_symbol_case():
    spec = get_instrument_spec("xauusd")

    assert spec.instrument == "XAUUSD"


def test_get_instrument_spec_rejects_unsupported_symbol():
    with pytest.raises(ValueError, match="Unsupported instrument: EURUSD"):
        get_instrument_spec("EURUSD")
