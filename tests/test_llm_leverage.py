import math

import pytest

from orum.llm.leverage import LeveragePolicyError, apply_leverage_policy


def test_twenty_x_paper_trade_stays_twenty_x_but_is_only_two_x_fr_eligible():
    result = apply_leverage_policy(
        requested=20,
        paper_min=1,
        paper_max=40,
        jurisdiction_profile="fr_retail",
        asset_class="crypto",
        product_kind="perpetual",
    )

    assert result.requested == 20
    assert result.paper_effective == 20
    assert result.fr_retail_eligible == 2
    assert result.excess_over_fr_retail == 18
    assert result.experimental_only is True
    assert result.clamp_reasons == ()


def test_requested_leverage_is_mechanically_clamped_without_using_confidence():
    result = apply_leverage_policy(
        requested=80,
        paper_min=1,
        paper_max=40,
        jurisdiction_profile="fr_retail",
        asset_class="crypto",
        product_kind="perpetual",
    )

    assert result.paper_effective == 40
    assert result.fr_retail_eligible == 2
    assert result.clamp_reasons == ("capped_at_paper_max",)


def test_paper_minimum_is_a_mechanical_clamp_too():
    result = apply_leverage_policy(
        requested=0.5,
        paper_min=1,
        paper_max=40,
        jurisdiction_profile="research_only",
        asset_class="crypto",
        product_kind="perpetual",
    )

    assert result.paper_effective == 1
    assert result.fr_retail_eligible is None
    assert result.excess_over_fr_retail is None
    assert result.clamp_reasons == ("raised_to_paper_min",)


@pytest.mark.parametrize(
    ("asset_class", "expected"),
    [
        ("major_fx", 30),
        ("non_major_fx", 20),
        ("gold", 20),
        ("major_index", 20),
        ("other_commodity", 10),
        ("non_major_equity_index", 10),
        ("equity", 5),
        ("other_reference", 5),
        ("crypto", 2),
    ],
)
def test_fr_retail_cfd_caps_follow_the_underlying_class(asset_class, expected):
    result = apply_leverage_policy(
        requested=1,
        paper_min=1,
        paper_max=40,
        jurisdiction_profile="fr_retail",
        asset_class=asset_class,
        product_kind="cfd",
    )

    assert result.fr_retail_eligible == expected


def test_unknown_live_product_has_no_invented_legal_eligibility():
    result = apply_leverage_policy(
        requested=10,
        paper_min=1,
        paper_max=40,
        jurisdiction_profile="fr_retail",
        asset_class="crypto",
        product_kind="structured_note",
    )

    assert result.paper_effective == 10
    assert result.fr_retail_eligible is None
    assert result.excess_over_fr_retail is None
    assert result.experimental_only is True
    assert "legal_eligibility_unverified" in result.clamp_reasons


@pytest.mark.parametrize(
    "values",
    [
        {"requested": 0, "paper_min": 1, "paper_max": 40},
        {"requested": math.nan, "paper_min": 1, "paper_max": 40},
        {"requested": 5, "paper_min": 0, "paper_max": 40},
        {"requested": 5, "paper_min": 10, "paper_max": 2},
    ],
)
def test_invalid_mechanical_bounds_are_rejected(values):
    with pytest.raises(LeveragePolicyError):
        apply_leverage_policy(
            **values,
            jurisdiction_profile="fr_retail",
            asset_class="crypto",
            product_kind="perpetual",
        )
