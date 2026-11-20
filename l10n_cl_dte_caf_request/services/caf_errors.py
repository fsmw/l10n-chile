# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Classified CAF acquisition outcomes shared by the client and the requests."""

from dataclasses import dataclass

PRE_DISPATCH = "pre_dispatch"
POST_DISPATCH = "post_dispatch"
VALIDATION = "validation"
# Pre-dispatch failures that may succeed when repeated.
RETRYABLE_CODES = frozenset(
    {
        "SII_TRANSPORT_ERROR",
        "SII_SESSION_EXPIRED",
        "SII_SESSION_LIMIT",
        "SII_ASSISTANT_ERROR",
        "INTERRUPTED_BEFORE_DISPATCH",
        "UNEXPECTED_ERROR",
    }
)


@dataclass
class SiiCafError(Exception):
    """A classified acquisition failure that never carries credentials."""

    code: str
    phase: str = PRE_DISPATCH
    uncertain: bool = False
    sii_code: str | None = None
    folio_start: int | None = None
    folio_end: int | None = None
    maximum_quantity: int | None = None

    def __str__(self):
        return self.code
