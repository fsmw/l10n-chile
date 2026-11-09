# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _
from odoo.exceptions import UserError

from .receiver_activity_correction import (
    correction_text,
    is_receiver_activity_correction,
)
from .sii_activity_catalogue import PROFILE, SiiActivityCatalogue


class SyntheticReceiverActivityService:
    """Plan consistent synthetic receiver data without modifying fiscal masters."""

    def build_plan(self, certification_set):
        cases = sorted(certification_set.case_ids, key=lambda item: item.sequence)
        sources = {
            item["identifier"]: item
            for item in certification_set.normalized_payload["cases"]
        }
        roots, protected = {}, set()
        for case in cases:
            reference = case.reference_case_identifier
            if reference and reference not in roots:
                raise UserError(
                    _(
                        "Resolve the earlier case reference before generating "
                        "activities."
                    )
                )
            root = roots.get(reference, case.case_identifier)
            roots[case.case_identifier] = root
            review = case.receiver_activity_correction or {}
            if case.note_operation == "text" and (
                sources[case.case_identifier].get("sii_reference_reason")
                or case.sii_reference_reason
                and review.get("profile") != PROFILE
                or not is_receiver_activity_correction(case)
            ):
                # Preserve the entire chain when either the SII or a human has
                # already supplied correction wording. Do not contradict it.
                protected.add(root)
        catalogue = SiiActivityCatalogue()
        context = catalogue.provenance()
        activities, corrections, result = {}, {}, []
        seed = f"{certification_set.source_digest}:{certification_set.attention_number}"
        for case in cases:
            if roots[case.case_identifier] in protected:
                continue
            if case.document_code not in {"33", "34", "56", "61"}:
                continue
            reference = case.reference_case_identifier
            activity = activities.get(reference)
            if activity is None:
                receiver_key = case.partner_id.id if case.partner_id else "unmapped"
                activity = catalogue.select(f"{seed}:receiver:{receiver_key}")
            correction = None
            if is_receiver_activity_correction(case):
                previous = activity
                activity = catalogue.select(
                    f"{seed}:{case.case_identifier}:corrected", previous["code"]
                )
                correction = {
                    **context,
                    "profile": PROFILE,
                    "previous_activity_code": previous["code"],
                    "previous_activity": previous["name"],
                    "corrected_activity_code": activity["code"],
                    "corrected_activity": activity["name"],
                    "verification_reference": context["source_url"],
                }
                corrections[case.case_identifier] = previous
            elif case.document_code == "56" and reference in corrections:
                activity = corrections[reference]
            activities[case.case_identifier] = activity
            result.append(
                {
                    "case_id": case.id,
                    "activity": {
                        **context,
                        "profile": PROFILE,
                        "origin": "synthetic_local",
                        "code": activity["code"],
                        "name": activity["name"],
                    },
                    "correction": correction,
                }
            )
        return result

    def correction_for_case(self, case):
        return next(
            (
                item["correction"]
                for item in self.build_plan(case.set_id)
                if item["case_id"] == case.id
            ),
            None,
        )

    def proposed_text(self, case):
        proposal = self.correction_for_case(case)
        if proposal:
            return correction_text(
                proposal["previous_activity"], proposal["corrected_activity"]
            )
        return None


def apply_receiver_activity_to_snapshot(snapshot, case):
    activity = case.synthetic_receiver_activity
    if not activity:
        return snapshot
    if (
        not case.set_id.contains_synthetic_completion
        or activity.get("origin") != "synthetic_local"
        or activity.get("profile") != PROFILE
        or not SiiActivityCatalogue().matches(
            activity.get("code"), activity.get("name")
        )
    ):
        raise UserError(
            _("The synthetic receiver activity is inconsistent with its catalogue.")
        )
    snapshot["receiver"]["activity"] = activity["name"]
    snapshot["certification"]["synthetic_receiver_activity"] = dict(activity)
    return snapshot
