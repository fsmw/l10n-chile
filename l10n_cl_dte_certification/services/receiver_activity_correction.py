# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import re

from odoo import _
from odoo.exceptions import ValidationError

from .set_parser import normalized_label
from .sii_activity_catalogue import PROFILE, SiiActivityCatalogue


def is_receiver_activity_correction(case):
    return (
        case.document_code == "61"
        and case.note_operation == "text"
        and re.fullmatch(
            r"CORRIGE GIRO(?: DEL RECEPTOR)?",
            normalized_label(case.reference_reason or ""),
        )
        is not None
    )


def correction_text(previous_activity, corrected_activity):
    activities = [previous_activity or "", corrected_activity or ""]
    if any(
        not value.strip() or len(value) > 400 or any(ord(char) < 32 for char in value)
        for value in activities
    ):
        raise ValidationError(
            _("Enter both receiver activities, up to 400 characters on one line each.")
        )
    previous, corrected = (value.strip() for value in activities)
    if previous.casefold() == corrected.casefold():
        raise ValidationError(_("The corrected receiver activity must be different."))
    text = (
        f"Donde dice: Giro del receptor: {previous}\n"
        f"Debe decir: Giro del receptor: {corrected}"
    )
    try:
        text.encode("ISO-8859-1")
    except UnicodeEncodeError as error:
        raise ValidationError(
            _("Receiver activities must use characters supported by ISO-8859-1.")
        ) from error
    return text


def has_reviewed_receiver_activity(case):
    review = getattr(case, "receiver_activity_correction", None) or {}
    if (
        not is_receiver_activity_correction(case)
        or review.get("origin") not in {"user_input", "synthetic_local"}
        or not review.get("verification_reference")
        or not review.get("actor_id")
        or not review.get("applied_at")
    ):
        return False
    if review["origin"] == "synthetic_local":
        catalogue = SiiActivityCatalogue()
        if (
            review.get("profile") != PROFILE
            or not catalogue.matches(
                review.get("previous_activity_code"), review.get("previous_activity")
            )
            or not catalogue.matches(
                review.get("corrected_activity_code"), review.get("corrected_activity")
            )
            or review.get("previous_activity_code")
            == review.get("corrected_activity_code")
        ):
            return False
    try:
        return case.sii_reference_reason == correction_text(
            review.get("previous_activity"), review.get("corrected_activity")
        )
    except ValidationError:
        return False


def unresolved_source_instructions(case, source_case):
    """Resolve only the missing giro wording through a traced human review."""
    diagnostics = source_case.get("unsupported_instructions", [])
    if source_case.get("sii_reference_reason") or not has_reviewed_receiver_activity(
        case
    ):
        return diagnostics
    return [
        item
        for item in diagnostics
        if item
        != {
            "source_line": source_case["source_start_line"],
            "text": "Text correction content not recognized",
        }
    ]
