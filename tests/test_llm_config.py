import pytest

from orum.llm.config import ConfigError, LlmMode, LlmTradingConfig


def test_default_config_is_off_and_aggressive_paper_cap_is_explicit():
    config = LlmTradingConfig.from_mapping({})

    assert config.mode is LlmMode.OFF
    assert config.paper_max_leverage == 40.0
    assert config.jurisdiction_profile == "fr_retail"
    assert config.model == "deepseek/deepseek-v4-pro"
    assert config.paper_starting_balance_usd == 10_000
    assert config.paper_fee_rate == 0.0005
    assert config.paper_maintenance_margin_rate == 0.005
    assert config.allow_stop_beyond_liquidation is False


@pytest.mark.parametrize(
    ("mapping", "message"),
    [
        ({"mode": "live"}, "mode"),
        ({"analyst_interval_minutes": 0}, "analyst_interval_minutes"),
        ({"request_timeout_seconds": float("nan")}, "request_timeout_seconds"),
        ({"max_parse_retries": -1}, "max_parse_retries"),
        ({"paper_min_leverage": 5, "paper_max_leverage": 4}, "paper_max_leverage"),
        ({"paper_min_leverage": 0.5}, "paper_min_leverage"),
        ({"paper_max_leverage": 41}, "paper_max_leverage"),
        ({"model": " "}, "model"),
        ({"paper_starting_balance_usd": 0}, "paper_starting_balance_usd"),
        ({"paper_fee_rate": -0.1}, "paper_fee_rate"),
        ({"paper_maintenance_margin_rate": 1}, "paper_maintenance_margin_rate"),
        ({"allow_stop_beyond_liquidation": "yes"}, "allow_stop_beyond_liquidation"),
        ({"unknown_option": True}, "unknown_option"),
    ],
)
def test_config_rejects_invalid_or_unknown_values(mapping, message):
    with pytest.raises(ConfigError, match=message):
        LlmTradingConfig.from_mapping(mapping)


def test_config_accepts_every_explicit_mode():
    assert {
        LlmTradingConfig.from_mapping({"mode": mode.value}).mode
        for mode in LlmMode
    } == set(LlmMode)
