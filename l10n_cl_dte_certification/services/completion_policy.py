# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hashlib

from .receiver_activity_correction import (
    correction_text,
    has_reviewed_receiver_activity,
)
from .sii_activity_catalogue import PROFILE, SiiActivityCatalogue


def _entries(case):
    return (getattr(case, "completion_provenance", None) or {}).get("entries", [])


def has_generated_completion(certification_set):
    return any(
        getattr(case, "synthetic_receiver_activity", False)
        or (getattr(case, "receiver_activity_correction", None) or {}).get("origin")
        == "synthetic_local"
        or any(item.get("origin") == "synthetic_local" for item in _entries(case))
        for case in getattr(certification_set, "case_ids", [])
    )


def _digest(value):
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _valid_metadata(value, catalogue):
    return (
        value.get("origin") == "synthetic_local"
        and value.get("profile") == PROFILE
        and value.get("actor_id")
        and value.get("applied_at")
        and all(
            value.get(key) == expected
            for key, expected in catalogue.provenance().items()
        )
    )


def _valid_entry(entry, catalogue):
    if not _valid_metadata(entry, catalogue):
        return False
    if entry.get("field") == "synthetic_receiver_activity":
        return catalogue.matches(entry.get("code"), entry.get("name")) and entry.get(
            "value_digest"
        ) == _digest(entry["name"])
    if entry.get("field") == "sii_reference_reason":
        if (
            not catalogue.matches(
                entry.get("previous_activity_code"), entry.get("previous_activity")
            )
            or not catalogue.matches(
                entry.get("corrected_activity_code"), entry.get("corrected_activity")
            )
            or entry.get("previous_activity_code")
            == entry.get("corrected_activity_code")
        ):
            return False
        return entry.get("value_digest") == _digest(
            correction_text(entry["previous_activity"], entry["corrected_activity"])
        )
    return False


def _trace_matches(value, entries, field, digest):
    return any(
        entry.get("field") == field
        and entry.get("value_digest") == digest
        and all(entry.get(key) == expected for key, expected in value.items())
        for entry in entries
    )


def catalogue_completions_allowed(certification_set, require_official_mode=True):
    """Allow only audited catalogue data in the enabled Certification boundary."""
    company = getattr(certification_set, "company_id", None)
    if (
        require_official_mode
        and getattr(certification_set, "completion_mode", None) != "official_sii"
    ):
        return False
    if (
        not getattr(certification_set, "contains_synthetic_completion", False)
        or getattr(certification_set, "sii_environment", None) != "certification"
        or getattr(company, "dte_sii_environment", None) != "certification"
        or not getattr(company, "dte_certification_enabled", False)
    ):
        return False
    cases = getattr(certification_set, "case_ids", [])
    by_identifier = {case.case_identifier: case for case in cases}
    catalogue = SiiActivityCatalogue()
    found = False
    for case in cases:
        entries = [
            entry
            for entry in _entries(case)
            if entry.get("origin") == "synthetic_local"
        ]
        if any(not _valid_entry(entry, catalogue) for entry in entries):
            return False
        activity = case.synthetic_receiver_activity or {}
        review = case.receiver_activity_correction or {}
        if not activity:
            if entries:
                return False
            continue
        found = True
        if (
            not _valid_metadata(activity, catalogue)
            or not catalogue.matches(activity.get("code"), activity.get("name"))
            or not _trace_matches(
                activity,
                entries,
                "synthetic_receiver_activity",
                _digest(activity["name"]),
            )
        ):
            return False
        target = by_identifier.get(case.reference_case_identifier)
        target_activity = (target.synthetic_receiver_activity or {}) if target else {}
        if case.note_operation == "text":
            if (
                not target_activity
                or not _valid_metadata(review, catalogue)
                or not has_reviewed_receiver_activity(case)
                or review.get("previous_activity_code") != target_activity.get("code")
                or review.get("previous_activity") != target_activity.get("name")
                or review.get("corrected_activity_code") != activity["code"]
                or review.get("corrected_activity") != activity["name"]
                or not _trace_matches(
                    review,
                    entries,
                    "sii_reference_reason",
                    _digest(case.sii_reference_reason),
                )
            ):
                return False
        elif target_activity:
            if case.document_code == "56" and target.note_operation == "text":
                target_review = target.receiver_activity_correction or {}
                expected = (
                    target_review.get("previous_activity_code"),
                    target_review.get("previous_activity"),
                )
            else:
                expected = (target_activity["code"], target_activity["name"])
            if (activity["code"], activity["name"]) != expected:
                return False
    return found


def catalogue_activity_matches_xml(root, case):
    activity = getattr(case, "synthetic_receiver_activity", None) or {}
    if not activity:
        return True
    receiver_activity = root.xpath(
        "string(./*[local-name()='Documento']/*[local-name()='Encabezado']/"
        "*[local-name()='Receptor']/*[local-name()='GiroRecep'])"
    )
    if receiver_activity != activity.get("name"):
        return False
    text = None
    if case.note_operation == "text":
        text = case.sii_reference_reason
    elif case.document_code == "56" and case.note_operation == "cancel":
        target = case.set_id.case_ids.filtered(
            lambda item: item.case_identifier == case.reference_case_identifier
        )
        if target and target.note_operation == "text":
            text = target.sii_reference_reason
    if text:
        details = root.xpath("./*[local-name()='Documento']/*[local-name()='Detalle']")
        return (
            len(details) == 1
            and details[0].xpath("string(./*[local-name()='DscItem'])") == text
            and details[0].xpath("string(./*[local-name()='MontoItem'])") == "0"
        )
    return True
