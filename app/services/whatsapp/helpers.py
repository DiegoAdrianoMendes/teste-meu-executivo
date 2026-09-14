from __future__ import annotations

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat

DEFAULT_PHONE_REGION = "BR"


def _canonicalize_brazilian_mobile(digits: str) -> str:
    """Use the ninth digit as the canonical form for Brazilian mobiles."""
    if not digits.startswith("55"):
        return digits
    if len(digits) == 12 and digits[4] in "6789":
        return f"{digits[:4]}9{digits[4:]}"
    return digits


def phone_number_variants(value: str | None) -> set[str]:
    """Return safe equality candidates, including Meta's BR mobile alias."""
    canonical = normalize_phone_number(value)
    if canonical is None:
        digits = "".join(character for character in str(value or "") if character.isdigit())
        return {digits} if 8 <= len(digits) <= 15 and not digits.startswith("55") else set()
    variants = {canonical}
    if (
        canonical.startswith("55")
        and len(canonical) == 13
        and canonical[4] == "9"
        and canonical[5] in "6789"
    ):
        variants.add(f"{canonical[:4]}{canonical[5:]}")
    return variants


def phone_number_lookup_variants(value: str | None) -> set[str]:
    """Return canonical aliases in both digits-only and E.164 storage forms."""
    variants = phone_number_variants(value)
    return variants | {f"+{variant}" for variant in variants}


def _parse_phone_candidate(value: str, region: str | None):
    try:
        parsed = phonenumbers.parse(value, region)
    except NumberParseException:
        return None
    if parsed.extension or not phonenumbers.is_possible_number(parsed):
        return None
    return parsed if phonenumbers.is_valid_number(parsed) else None


def normalize_phone_number(
    value: str | None,
    *,
    default_region: str = DEFAULT_PHONE_REGION,
) -> str | None:
    """Return the Meta recipient format: valid E.164 digits without ``+``.

    Brazilian operator input may omit country code but must include DDD. An
    explicit ``+``/``00`` always wins; long unformatted values are treated as
    international. This is deterministic validation, not an LLM guess.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    digits = "".join(character for character in raw if character.isdigit())
    if not digits:
        return None
    digits = _canonicalize_brazilian_mobile(digits)

    if raw.startswith("+"):
        candidates = [(f"+{digits}", None)]
    elif raw.startswith("00") and len(digits) > 2:
        candidates = [(f"+{digits[2:]}", None)]
    elif len(digits) >= 12 or (digits.startswith("55") and len(digits) in {12, 13}):
        candidates = [(f"+{digits}", None)]
    else:
        candidates = [(digits, default_region), (f"+{digits}", None)]

    for candidate, region in candidates:
        parsed = _parse_phone_candidate(candidate, region)
        if parsed is not None:
            formatted = phonenumbers.format_number(parsed, PhoneNumberFormat.E164).lstrip("+")
            return _canonicalize_brazilian_mobile(formatted)
    return None


def normalize_meta_phone_number(value: str | None) -> str | None:
    """Validate a number already supplied by Meta in international format."""
    raw = str(value or "").strip()
    digits = "".join(character for character in raw if character.isdigit())
    if not digits:
        return None
    digits = _canonicalize_brazilian_mobile(digits)
    parsed = _parse_phone_candidate(f"+{digits}", None)
    if parsed is None:
        return None
    formatted = phonenumbers.format_number(parsed, PhoneNumberFormat.E164).lstrip("+")
    return _canonicalize_brazilian_mobile(formatted)


def format_phone_number(value: str | None) -> str | None:
    canonical = normalize_phone_number(value)
    if canonical is None:
        return None
    parsed = _parse_phone_candidate(f"+{canonical}", None)
    return (
        phonenumbers.format_number(parsed, PhoneNumberFormat.INTERNATIONAL)
        if parsed is not None
        else None
    )


def build_last_error(code: str | None, message: str | None) -> str | None:
    if code and message:
        return f"{code}: {message}"
    return message or code
