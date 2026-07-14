"""Language-boundary checks for model-owned narratives."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


class ModelLanguageError(ValueError):
    """Raised when a model narrative contains disallowed CJK text."""


_CJK_NARRATIVE_RANGES = (
    (0x1100, 0x11FF),  # Hangul Jamo
    (0x3000, 0x303F),  # CJK Symbols and Punctuation
    (0x3040, 0x30FF),  # Hiragana and Katakana
    (0x3130, 0x318F),  # Hangul Compatibility Jamo
    (0x31F0, 0x31FF),  # Katakana Phonetic Extensions
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xA960, 0xA97F),  # Hangul Jamo Extended-A
    (0xAC00, 0xD7FF),  # Hangul Syllables and Jamo Extended-B
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0xFF66, 0xFF9F),  # Halfwidth Katakana
    (0xFFA0, 0xFFDC),  # Halfwidth Hangul Jamo
    (0x1AFF0, 0x1AFFF),  # Kana Extended-B
    (0x1B000, 0x1B16F),  # Kana Supplement and Extended-A
    (0x20000, 0x2FA1F),  # CJK Unified Ideograph Extensions B-F and I
    (0x30000, 0x323AF),  # CJK Unified Ideograph Extensions G and H
)


def _contains_cjk(value: str) -> bool:
    return any(
        start <= ord(character) <= end
        for character in value
        for start, end in _CJK_NARRATIVE_RANGES
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
