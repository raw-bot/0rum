"""Versioned hypotheses for paper learning, not automatic risk gates."""
import re

RULES = {
    "confirm_entry": "Avant une entrée anticipée, examiner une confirmation par clôture et réaction du prix au niveau identifié. Comparer son coût d'attente au risque de faux départ ; ne pas transformer cette hypothèse en veto systématique.",
    "wait_pullback": "Après une extension ou une cassure, comparer une entrée sur retest confirmé à une poursuite immédiate. Expliciter le niveau et l'invalidation ; ne pas supposer qu'un pullback aura lieu.",
    "target_within_horizon": "Si les objectifs nécessitent de franchir des obstacles proches ou dépassent l'horizon prévu, examiner des prises de profit intermédiaires atteignables. Ne pas réduire les objectifs lorsque la continuation est confirmée sans justifier ce choix.",
    "stop_beyond_noise": "Si le stop paraît exposé au bruit autour d'un niveau évident, comparer un stop au-delà de l'invalidation structurelle avec une marge liée à la volatilité. Adapter la quantité au risque prévu ; ne pas augmenter le budget de risque pour élargir le stop.",
    "let_winners_run": "Si une tendance forte et son momentum restent confirmés, comparer une cible plus lointaine ou une protection suiveuse aux prises de profit précoces. Conserver une invalidation explicite ; ne pas prolonger un mouvement dont le momentum s'épuise.",
    "shorten_stagnant_trade": "Si le mouvement attendu ne progresse pas à l'horizon prévu, examiner une réduction ou une sortie temporelle plutôt qu'une prolongation non justifiée. Expliciter le critère de stagnation.",
}
ALLOWED_ERRORS = {
    "confirm_entry": {"poor_timing", "wrong_direction", "price_reaction_ignored", "narrative_misread"},
    "wait_pullback": {"poor_timing", "price_reaction_ignored"},
    "target_within_horizon": {"poor_timing", "target_too_ambitious"},
    "stop_beyond_noise": {"stop_too_tight"},
    "let_winners_run": {"target_too_conservative"},
    "shorten_stagnant_trade": {"poor_timing", "target_too_ambitious"},
}
OPPOSITES = {frozenset({"target_within_horizon", "let_winners_run"})}
# Exact aliases only: ambiguous multi-regime narratives stay separate.
REGIME_ALIASES = {
    "trend_down": "downtrend", "trending_down": "downtrend", "bearish": "downtrend",
    "trend_up": "uptrend", "trending_up": "uptrend", "bullish": "uptrend",
    "trend_down_high_vol": "downtrend_high_vol",
    "downtrend with high volatility": "downtrend_high_vol",
    "range_breakdown_4h": "range_breakdown",
    "range_breakout_bullish": "range_expansion_bullish",
}


def regime_key(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip().casefold())
    return REGIME_ALIASES.get(normalized, normalized)


def scope(case) -> tuple[str, str, str]:
    return case.symbol, regime_key(case.regime), case.side


def validate_rule(key: str | None, error: str) -> None:
    if key is not None and (key not in RULES or error not in ALLOWED_ERRORS[key]):
        raise ValueError("lesson adjustment code is incompatible with its error category")
