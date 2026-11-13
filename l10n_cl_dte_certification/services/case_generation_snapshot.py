# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Snapshot validation shared by the case generation service."""

from odoo import _
from odoo.exceptions import ValidationError


class CaseGenerationSnapshotMixin:
    """Prove a referenced case snapshot still matches its generated move."""

    def _validate_text_correction_snapshot(self, line, selected_lines):
        target = line.referenced_line_id
        if (
            not target
            or target.run_id != line.run_id
            or target.document_code != "33"
            or target.sequence >= line.sequence
        ):
            raise ValidationError(
                _(
                    "Text-correction case %(case)s requires an earlier type-33 "
                    "invoice in the same execution.",
                    case=line.case_identifier,
                )
            )
        if target.generation_state == "pending" and target not in selected_lines:
            raise ValidationError(
                _(
                    "Select the referenced invoice along with text-correction "
                    "case %(case)s.",
                    case=line.case_identifier,
                )
            )
        if target.generation_state not in {"pending", "generated"}:
            raise ValidationError(
                _(
                    "The referenced invoice for text-correction case %(case)s "
                    "is not available for draft generation.",
                    case=line.case_identifier,
                )
            )
        if (
            line.snapshot.get("reference_code") != "2"
            or line.snapshot.get("reference_target_document_code") != "33"
            or not line.snapshot.get("reference_reason")
        ):
            raise ValidationError(
                _(
                    "Text-correction case %(case)s has incomplete reference data.",
                    case=line.case_identifier,
                )
            )
        content = line.snapshot.get("text_correction_content") or []
        if sorted(item.get("kind") for item in content) != [
            "debe_decir",
            "dice",
        ] or any(not item.get("value") for item in content):
            raise ValidationError(
                _(
                    "Text-correction case %(case)s has incomplete correction text.",
                    case=line.case_identifier,
                )
            )
        if line.snapshot_digest != self._snapshot_digest(line.snapshot):
            raise ValidationError(
                _(
                    "Case %(case)s no longer matches its execution snapshot.",
                    case=line.case_identifier,
                )
            )
        if line.source_digest != line.run_id.set_digest:
            raise ValidationError(
                _(
                    "Case %(case)s no longer matches its original SET.",
                    case=line.case_identifier,
                )
            )

    def _validate_note_cancellation_snapshot(self, line, selected_lines):
        target = line.referenced_line_id
        if (
            not target
            or target.run_id != line.run_id
            or target.document_code != "61"
            or target.sequence >= line.sequence
            or target.snapshot.get("note_operation") not in {"text", "return", "cancel"}
        ):
            raise ValidationError(
                _(
                    "Type-56 case %(case)s must cancel an earlier type-61 "
                    "case in the same execution.",
                    case=line.case_identifier,
                )
            )
        if target.generation_state == "pending" and target not in selected_lines:
            raise ValidationError(
                _(
                    "Select the referenced type-61 correction along with case "
                    "%(case)s.",
                    case=line.case_identifier,
                )
            )
        if target.generation_state not in {"pending", "generated"}:
            raise ValidationError(
                _(
                    "The referenced type-61 correction for case %(case)s is not "
                    "available for draft generation.",
                    case=line.case_identifier,
                )
            )
        if (
            line.snapshot.get("reference_code") != "1"
            or line.snapshot.get("reference_target_document_code") != "61"
            or not line.snapshot.get("reference_reason")
            or line.snapshot.get("text_correction_content")
            or target.snapshot_digest != self._snapshot_digest(target.snapshot)
            or target.source_digest != line.run_id.set_digest
        ):
            raise ValidationError(
                _(
                    "Type-56 cancellation case %(case)s has inconsistent reference "
                    "data.",
                    case=line.case_identifier,
                )
            )
        operation = target.snapshot.get("note_operation")
        if operation in {"return", "cancel"} and (
            not target.referenced_line_id
            or target.referenced_line_id.run_id != line.run_id
            or target.referenced_line_id.document_code != "33"
            or target.snapshot.get("reference_target_document_code") != "33"
            or target.snapshot.get("reference_code")
            != ("3" if operation == "return" else "1")
        ):
            raise ValidationError(
                _(
                    "Type-56 case %(case)s references a type-61 note with an "
                    "inconsistent invoice root.",
                    case=line.case_identifier,
                )
            )
        if line.snapshot_digest != self._snapshot_digest(line.snapshot):
            raise ValidationError(
                _(
                    "Case %(case)s no longer matches its execution snapshot.",
                    case=line.case_identifier,
                )
            )
        if line.source_digest != line.run_id.set_digest:
            raise ValidationError(
                _(
                    "Case %(case)s no longer matches its original SET.",
                    case=line.case_identifier,
                )
            )
