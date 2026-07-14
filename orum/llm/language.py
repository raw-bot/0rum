"""Language-boundary checks for model-owned narratives."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import unicodedata


class ModelLanguageError(ValueError):
    """Raised when a model narrative contains disallowed CJK text."""


_CJK_NAME_PREFIXES = (
    "CJK UNIFIED IDEOGRAPH",
    "CJK COMPATIBILITY IDEOGRAPH",
    "CJK RADICAL",
    "KANGXI RADICAL",
    "CJK STROKE",
)
_SCRIPT_NAME_MARKERS = ("HIRAGANA", "KATAKANA", "HANGUL")
_SCRIPT_MARK_RANGES = (
    (0x3000, 0x303F),  # CJK Symbols and Punctuation
    (0x16FE0, 0x16FFF),  # Ideographic symbols and Vietnamese reading marks
)
# These blocks were added after the Unicode database bundled with Python 3.11.
# Keep explicit ranges so the language boundary is stable across supported
# Python runtimes, including when ``unicodedata.name`` returns an empty value.
_HAN_FALLBACK_RANGES = (
    (0x31350, 0x323AF),  # CJK Unified Ideographs Extension H
    (0x323B0, 0x33479),  # CJK Unified Ideographs Extension J
)


def _has_cjk_script_name(character: str) -> bool:
    name = unicodedata.name(character, "")
    return name.startswith(_CJK_NAME_PREFIXES) or any(
        marker in name for marker in _SCRIPT_NAME_MARKERS
    )


def _contains_cjk(value: str) -> bool:
    return any(
        _has_cjk_script_name(character)
        or any(start <= ord(character) <= end for start, end in _SCRIPT_MARK_RANGES)
        or any(start <= ord(character) <= end for start, end in _HAN_FALLBACK_RANGES)
        for character in value
    )


def ensure_no_cjk_narrative(value: object) -> None:
    """Reject CJK characters in a narrative value and its nested text values."""
    if isinstance(value, str):
        if _contains_cjk(value):
            raise ModelLanguageError("CJK characters are not permitted in French narratives")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            ensure_no_cjk_narrative(key)
            ensure_no_cjk_narrative(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            ensure_no_cjk_narrative(item)
