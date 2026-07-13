"""Independent paper-leverage and French-retail eligibility calculations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date


class LeveragePolicyError(ValueError):
    """Raised when mechanical leverage bounds cannot be evaluated safely."""


# Official retail CFD class caps:
# https://www.esma.europa.eu/press-news/esma-news/esma-adopts-final-product-intervention-measures-cfds-and-binary-options
# The AMF confirms the French retail examples, including 2x for crypto CFDs:
# https://www.amf-france.org/fr/espace-epargnants/comprendre-les-produits-financiers/produits-complexes/cfd
FR_RETAIL_CFD_CAPS: dict[str, float] = {
    "major_fx": 30.0,
    "non_major_fx": 20.0,
    "gold": 20.0,
    "major_index": 20.0,
    "other_commodity": 10.0,
    "non_major_equity_index": 10.0,
    "equity": 5.0,
    "other_reference": 5.0,
    "crypto": 2.0,
}

# ESMA's 2026 statement says leveraged perpetuals are likely to fall within
# national CFD measures when their economic characteristics meet the CFD
# definition. The conservative research profile therefore uses the crypto-CFD
# cap until a product-specific legal review says otherwise:
# https://www.esma.europa.eu/press-news/esma-news/esma-reminds-firms-their-obligations-under-cfd-product-intervention-measures
FR_RETAIL_POLICY_REVIEW_DATE = date(2026, 7, 13)


@dataclass(frozen=True, slots=True)
class LeverageResult:
    requested: float
    paper_effective: float
    fr_retail_eligible: float | None
    excess_over_fr_retail: float | None
    experimental_only: bool
    clamp_reasons: tuple[str, ...]
    jurisdiction_profile: str
    asset_class: str
    product_kind: str
    evaluation_date: date

    def to_mapping(self) -> dict[str, object]:
        return {
            "requested": self.requested,
            "paper_effective": self.paper_effective,
            "fr_retail_eligible": self.fr_retail_eligible,
            "excess_over_fr_retail": self.excess_over_fr_retail,
            "experimental_only": self.experimental_only,
            "clamp_reasons": list(self.clamp_reasons),
            "jurisdiction_profile": self.jurisdiction_profile,
            "asset_class": self.asset_class,
            "product_kind": self.product_kind,
            "evaluation_date": self.evaluation_date.isoformat(),
        }


def _positive_finite(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise LeveragePolicyError(f"{name} must be a positive finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise LeveragePolicyError(f"{name} must be a positive finite number") from exc
    if not math.isfinite(number) or number <= 0:
        raise LeveragePolicyError(f"{name} must be a positive finite number")
    return number


def _fr_retail_cap(*, asset_class: str, product_kind: str) -> float | None:
    if product_kind == "cfd":
        return FR_RETAIL_CFD_CAPS.get(asset_class)
    if product_kind == "perpetual" and asset_class == "crypto":
        return FR_RETAIL_CFD_CAPS["crypto"]
    return None


def apply_leverage_policy(
    *,
    requested: float,
    paper_min: float,
    paper_max: float,
    jurisdiction_profile: str,
    asset_class: str,
    product_kind: str,
) -> LeverageResult:
    """Clamp only the paper experiment; annotate legal eligibility separately.

    This profile is a software classification aid, not legal advice. It never
    enables real execution and never infers professional-client status.
    """

    requested_value = _positive_finite(requested, "requested")
    minimum = _positive_finite(paper_min, "paper_min")
    maximum = _positive_finite(paper_max, "paper_max")
    if minimum > maximum:
        raise LeveragePolicyError("paper_min must not exceed paper_max")
    if not isinstance(jurisdiction_profile, str) or not jurisdiction_profile.strip():
        raise LeveragePolicyError("jurisdiction_profile must be non-empty")
    if not isinstance(asset_class, str) or not asset_class.strip():
        raise LeveragePolicyError("asset_class must be non-empty")
    if not isinstance(product_kind, str) or not product_kind.strip():
        raise LeveragePolicyError("product_kind must be non-empty")

    reasons: list[str] = []
    paper_effective = requested_value
    if requested_value < minimum:
        paper_effective = minimum
        reasons.append("raised_to_paper_min")
    elif requested_value > maximum:
        paper_effective = maximum
        reasons.append("capped_at_paper_max")

    eligibility = None
    if jurisdiction_profile == "fr_retail":
        eligibility = _fr_retail_cap(
            asset_class=asset_class.strip(),
            product_kind=product_kind.strip(),
        )
        if eligibility is None:
            reasons.append("legal_eligibility_unverified")

    excess = None if eligibility is None else max(0.0, paper_effective - eligibility)
    experimental_only = eligibility is None or paper_effective > eligibility
    return LeverageResult(
        requested=requested_value,
        paper_effective=paper_effective,
        fr_retail_eligible=eligibility,
        excess_over_fr_retail=excess,
        experimental_only=experimental_only,
        clamp_reasons=tuple(reasons),
        jurisdiction_profile=jurisdiction_profile.strip(),
        asset_class=asset_class.strip(),
        product_kind=product_kind.strip(),
        evaluation_date=FR_RETAIL_POLICY_REVIEW_DATE,
    )
