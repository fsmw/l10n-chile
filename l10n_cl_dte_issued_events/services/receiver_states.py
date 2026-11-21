# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Receiver state of an issued DTE derived from its receiver events.

The SII registry of Law 20.956 is the legal record: its events take precedence
over the informative responses that a receiver emails back. Without a claim in
the eight calendar days after the SII received the DTE, the invoice is
irrevocably (tacitly) accepted.
"""

import re
from datetime import datetime, timedelta

import pytz

# Issued DTE types the acceptance and claim registry answers for.
REGISTRY_DOCUMENT_CODES = ("33", "34", "43", "46")
REGISTRY_ENVIRONMENTS = ("certification", "production")
LEGAL_WINDOW = timedelta(days=8)
# One last registry check after the window closes captures last-day events.
FINAL_CHECK_GRACE = timedelta(days=1)
BACKOFF_BASE = timedelta(hours=1)
BACKOFF_MAX = timedelta(hours=24)
CHILE_TZ = pytz.timezone("America/Santiago")
EVENT_CODE_RE = re.compile(r"^[A-Z][A-Z_]{1,31}$")

RECEIVER_STATES = [
    ("none", "Not Tracked"),
    ("pending", "Awaiting Receiver"),
    ("accepted", "Accepted"),
    ("accepted_objections", "Accepted with Objections"),
    ("goods_received", "Goods Received"),
    ("tacit", "Tacitly Accepted"),
    ("paid", "Paid in Cash"),
    ("assigned", "Assigned"),
    ("rejected", "Rejected by Email"),
    ("claimed_content", "Content Claimed"),
    ("claimed_partial", "Partial Lack of Goods Claimed"),
    ("claimed_total", "Total Lack of Goods Claimed"),
]

# (source, event code) -> receiver state the event supports.
EVENT_STATES = {
    ("sii", "ACD"): "accepted",
    ("sii", "ERM"): "goods_received",
    ("sii", "RCD"): "claimed_content",
    ("sii", "RFP"): "claimed_partial",
    ("sii", "RFT"): "claimed_total",
    ("sii", "PAG"): "paid",
    ("sii", "CED"): "assigned",
    ("email", "RESULT_ACCEPTED"): "accepted",
    ("email", "RESULT_OBJECTIONS"): "accepted_objections",
    ("email", "RESULT_REJECTED"): "rejected",
    ("email", "GOODS_RECEIPT"): "goods_received",
}
# The strongest state wins; claims outrank any acceptance.
STATE_PRIORITY = (
    "claimed_total",
    "claimed_partial",
    "claimed_content",
    "rejected",
    "assigned",
    "paid",
    "accepted",
    "accepted_objections",
    "goods_received",
)
CLAIM_STATES = frozenset(
    {"claimed_total", "claimed_partial", "claimed_content", "rejected"}
)


def event_state(source, code):
    return EVENT_STATES.get((source, code))


def is_claim(source, code):
    return event_state(source, code) in CLAIM_STATES


def _strongest(states):
    for state in STATE_PRIORITY:
        if state in states:
            return state
    return False


def derive_receiver_state(events, deadline, now, tracked):
    """Return the receiver state for ``(source, code)`` pairs.

    ``tracked`` tells whether the DTE is followed in the SII registry; only then
    can it be pending or tacitly accepted.
    """
    registry = {event_state(source, code) for source, code in events if source == "sii"}
    emailed = {
        event_state(source, code) for source, code in events if source == "email"
    }
    state = _strongest(registry) or _strongest(emailed)
    if state:
        return state
    if not tracked or not deadline:
        return "none"
    return "tacit" if now >= deadline else "pending"


def registry_tracking_finished(codes):
    """Return whether no later registry event can change the receiver state."""
    codes = set(codes)
    return bool(codes & {"RCD", "RFT"}) or {"ACD", "ERM"} <= codes


def next_check(now, check_count, deadline):
    """Return ``(next check, finished)`` with exponential backoff in the window."""
    if not deadline or now >= deadline:
        return False, True
    exponent = min(max(check_count - 1, 0), 10)
    delay = min(BACKOFF_BASE * (2**exponent), BACKOFF_MAX)
    return min(now + delay, deadline), False


def chile_datetime_to_utc(value):
    """Parse an SII local timestamp into a naive UTC datetime, or ``False``."""
    text = (value or "").strip().replace("T", " ")
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, pattern)
        except ValueError:
            continue
        localized = CHILE_TZ.localize(parsed)
        return localized.astimezone(pytz.utc).replace(tzinfo=None)
    return False
