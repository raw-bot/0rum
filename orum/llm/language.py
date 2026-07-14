"""Language-boundary checks for model-owned narratives."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


class ModelLanguageError(ValueError):
    """Raised when a model narrative contains disallowed CJK text."""


def _contains_cjk(value: str) -> bool:
    for character in value:
        codepoint = ord(character)
        if (
            0x1100 <= codepoint <= 0x11FF  # Hangul Jamo
            or 0x2E80 <= codepoint <= 0x2FDF  # CJK Radicals and Ideographic Description
            or 0x3040 <= codepoint <= 0x30FF  # Hiragana and Katakana
            or 0x3100 <= codepoint <= 0x31FF  # Bopomofo and Katakana Phonetic Extensions
            or 0x3130 <= codepoint <= 0x318F  # Hangul Compatibility Jamo
            or 0x31A0 <= codepoint <= 0x31BF  # Bopomofo Extended
            or 0x3400 <= codepoint <= 0x4DBF  # CJK Unified Ideographs Extension A
            or 0x4E00 <= codepoint <= 0x9FFF  # CJK Unified Ideographs
            or 0xA960 <= codepoint <= 0xA97F  # Hangul Jamo Extended-A
            or 0xAC00 <= codepoint <= 0xD7FF  # Hangul syllables and Extended-B
            or 0xF900 <= codepoint <= 0xFAFF  # CJK Compatibility Ideographs
            or 0xFE30 <= codepoint <= 0xFE4F  # CJK Compatibility Forms
            or 0xFF66 <= codepoint <= 0xFF9F  # Halfwidth Katakana
            or 0x20000 <= codepoint <= 0x2FA1F  # CJK Extensions B through I
            or 0x30000 <= codepoint <= 0x323AF  # CJK Extension G and later
            or 0x1B000 <= codepoint <= 0x1B16F  # Kana Supplement and Extended-A
            or 0x1B170 <= codepoint <= 0x1B2FF  # Kana Extended-B
        ):
            return True
    return False


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
