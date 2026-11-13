# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Referenced-document checks for cancellation and correction cases."""

from odoo import _
from odoo.exceptions import ValidationError


class CaseGenerationCancellationMixin:
    """Prove a cancellation or correction case points at one valid document."""

    def _check_duplicate_note_cancellation(self, line, target, selected_lines):
        duplicate = line.run_id.line_ids.filtered(
            lambda item: (
                item != line
                and item.document_code == "56"
                and item.referenced_line_id == target
                and (item.generation_state == "generated" or item in selected_lines)
            )
        )
        if duplicate:
            raise ValidationError(
                _(
                    "Type-61 case %(case)s already has another selected or "
                    "generated type-56 cancellation.",
                    case=target.case_identifier,
                )
            )

    def _check_monetary_note_cancellation_target(self, line, target, root, rows):
        note = target.move_id
        if (
            target.generation_state != "generated"
            or not note
            or note.state != "draft"
            or note.move_type != "out_refund"
            or note.l10n_latam_document_type_id.code != "61"
            or note.dte_certification_run_line_id != target
            or note.reversed_entry_id != root.move_id
            or note.partner_id != root.partner_id
            or target.partner_id != root.partner_id
            or not root.move_id
            or root.move_id.state != "draft"
        ):
            raise ValidationError(
                _(
                    "Type-56 case %(case)s requires its earlier generated "
                    "monetary type-61 draft and matching invoice root.",
                    case=line.case_identifier,
                )
            )
        self._assert_move_matches_snapshot_rows(note, rows, target)
        return note

    def _check_text_correction_target(self, line, selected_lines):
        target = line.referenced_line_id
        if (
            target
            and target.generation_state == "pending"
            and target not in selected_lines
        ):
            raise ValidationError(
                _(
                    "Select the referenced invoice along with text-correction "
                    "case %(case)s.",
                    case=line.case_identifier,
                )
            )
        if (
            not target
            or target.run_id != line.run_id
            or target.document_code != "33"
            or target.sequence >= line.sequence
            or target.generation_state != "generated"
            or not target.move_id
            or target.move_id.state != "draft"
            or target.move_id.dte_certification_run_line_id != target
            or target.move_id.move_type != "out_invoice"
        ):
            raise ValidationError(
                _(
                    "Text-correction case %(case)s requires its earlier generated "
                    "type-33 draft in the same execution.",
                    case=line.case_identifier,
                )
            )
        return target

    def _check_text_cancellation_target(self, line, selected_lines):
        target = line.referenced_line_id
        if (
            target
            and target.generation_state == "pending"
            and target not in selected_lines
        ):
            raise ValidationError(
                _(
                    "Select the referenced type-61 correction along with case "
                    "%(case)s.",
                    case=line.case_identifier,
                )
            )
        if (
            not target
            or target.run_id != line.run_id
            or target.document_code != "61"
            or target.snapshot.get("note_operation") != "text"
            or target.sequence >= line.sequence
            or target.generation_state != "generated"
            or not target.move_id
            or target.move_id.state != "draft"
            or target.move_id.dte_certification_run_line_id != target
            or target.move_id.move_type != "out_refund"
            or target.move_id.reversed_entry_id != target.referenced_line_id.move_id
            or not target.partner_id
            or target.move_id.partner_id != target.partner_id
            or target.move_id.amount_total != 0
        ):
            raise ValidationError(
                _(
                    "Type-56 case %(case)s requires its earlier generated "
                    "zero-value type-61 text correction draft.",
                    case=line.case_identifier,
                )
            )
        return target
