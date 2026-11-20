# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Pure value helpers shared by the RCV normalizer and the CSV parser."""

import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

RUT_PATTERN = re.compile(r"^(\d{1,8})-([\dK])$")
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%Y%m%d", "%d-%m-%Y")
RUT_WEIGHTS_CYCLE = 6
RUT_MODULUS = 11


def normalize_vat(value, check_digit=None):
    """Return a Chilean RUT as ``12345678-9``.

    Dots, spaces and a ``CL`` country prefix are removed. When the value has
    no dash, ``check_digit`` is appended or the last character is used.
    """
    raw = "".join(str(value or "").split()).replace(".", "").upper()
    if raw.startswith("CL"):
        raw = raw[2:]
    if "-" in raw:
        number, digit = raw.rsplit("-", 1)
        return f"{number.strip('-')}-{digit}"
    if check_digit not in (None, ""):
        return f"{raw}-{str(check_digit).strip().upper()}"
    if len(raw) > 1:
        return f"{raw[:-1]}-{raw[-1]}"
    return raw


def rut_check_digit(number):
    """Return the modulo 11 check digit of a RUT number."""
    total = 0
    for index, digit in enumerate(reversed(str(number))):
        total += int(digit) * (2 + index % RUT_WEIGHTS_CYCLE)
    result = RUT_MODULUS - total % RUT_MODULUS
    return {11: "0", 10: "K"}.get(result, str(result))


def is_valid_rut(vat):
    """Tell whether a normalized RUT has a valid format and check digit."""
    match = RUT_PATTERN.match(vat or "")
    if not match:
        return False
    return rut_check_digit(match.group(1)) == match.group(2)


def parse_date(value):
    """Parse an SII date in any of its usual formats, or return False."""
    if isinstance(value, date):
        return value
    if not value:
        return False
    text = str(value).strip()[:10]
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue
    return False


def to_integer(value):
    """Return an SII amount as an integer.

    Empty values are zero. Decimal commas are accepted, but a fractional
    amount raises ``ValueError``: the SII reports whole pesos only.
    """
    if value in (None, "", False):
        return 0
    if isinstance(value, int):
        return value
    text = str(value).strip().replace(",", ".")
    try:
        number = Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f"not a number: {value!r}") from error
    if not number.is_finite() or number != number.to_integral_value():
        raise ValueError(f"not a whole amount: {value!r}")
    return int(number)


def payload_hash(payload):
    """Return a stable SHA-256 of a JSON-serializable payload."""
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
