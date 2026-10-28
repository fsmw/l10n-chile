# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Canonical identity helpers for Chilean electronic documents."""

import re
from dataclasses import dataclass

# Types supported by the MiPyme emitted-document endpoint.  Keep this list
# explicit: accepting arbitrary values would make portal and invoice matching
# unsafe.
DTE_TYPES = frozenset(
    {
        "33",
        "34",
        "39",
        "41",
        "43",
        "46",
        "52",
        "56",
        "61",
        "110",
        "111",
        "112",
    }
)


@dataclass(frozen=True)
class DteIdentity:
    issuer_rut: str
    dte_type: str
    folio: int


def normalize_rut(value):
    """Return a canonical ``digits-DV`` RUT and reject invalid input."""
    text = str(value or "").strip().upper().replace(".", "").replace(" ", "")
    if text.startswith("CL"):
        text = text[2:]
    if "-" in text:
        number, verifier = text.rsplit("-", 1)
    elif len(text) > 1:
        number, verifier = text[:-1], text[-1]
    else:
        raise ValueError("El RUT debe incluir dígitos y dígito verificador.")
    if not number.isdigit() or len(number) < 1 or verifier not in "0123456789K":
        raise ValueError("El RUT no tiene un formato válido.")
    expected = _rut_verifier(number)
    if verifier != expected:
        raise ValueError("El dígito verificador del RUT no es válido.")
    return f"{int(number)}-{verifier}"


def foreign_identifier_key(value, country_code):
    """Return the comparison key for a foreign tax identifier.

    Foreign identifiers are not Chilean RUTs.  Until a country-specific rule is
    added, preserve punctuation and significant characters and only trim
    surrounding whitespace.  Paraguay commonly includes a separator before
    the final digit, so accept that one representation without dropping zeros.
    """
    text = str(value or "").strip()
    if str(country_code or "").strip().upper() == "PY" and re.fullmatch(
        r"\d+(?:-\d)?", text
    ):
        return text.replace("-", "")
    return text


def split_rut(value):
    canonical = normalize_rut(value)
    number, verifier = canonical.split("-")
    return number, verifier


def normalize_folio(value):
    text = str(value or "").strip()
    if not re.fullmatch(r"\d+", text):
        raise ValueError("El folio debe ser un número entero positivo.")
    folio = int(text)
    if folio <= 0:
        raise ValueError("El folio debe ser mayor que cero.")
    return folio


def normalize_dte_type(value):
    code = str(value or "").strip()
    if code not in DTE_TYPES:
        raise ValueError("El tipo de DTE no es un tipo electrónico chileno válido.")
    return code


def _rut_verifier(number):
    factor = 2
    total = 0
    for digit in reversed(number):
        total += int(digit) * factor
        factor = 2 if factor == 7 else factor + 1
    remainder = 11 - (total % 11)
    return "0" if remainder == 11 else "K" if remainder == 10 else str(remainder)
