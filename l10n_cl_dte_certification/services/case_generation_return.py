# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Referenced-document checks for return and invoice cancellation cases."""

from odoo import _
from odoo.exceptions import ValidationError


class CaseGenerationReturnMixin:
    """Prove a return or invoice cancellation points at one valid document."""

    def _check_return_target(self, line, selected_lines):
        target = line.referenced_line_id
        if (
            target
            and target.generation_state == "pending"
            and target not in selected_lines
        ):
            raise ValidationError(
                _(
                    "Select the referenced invoice along with return case %(case)s.",
                    case=line.case_identifier,
                )
            )
        if (
            not target
            or target.run_id != line.run_id
            or target.document_code != "33"
            or target.sequence >= line.sequence
            or line.snapshot.get("reference_code") != "3"
            or line.snapshot.get("reference_target_document_code") != "33"
            or not line.snapshot.get("reference_reason")
            or target.generation_state not in {"pending", "generated"}
            or line.snapshot_digest != self._snapshot_digest(line.snapshot)
            or line.source_digest != line.run_id.set_digest
            or target.snapshot_digest != self._snapshot_digest(target.snapshot)
            or target.source_digest != line.run_id.set_digest
        ):
            raise ValidationError(
                _(
                    "Return case %(case)s requires a valid earlier "
                    "type-33 invoice in this execution.",
                    case=line.case_identifier,
                )
            )
        if target.generation_state == "generated" and (
            not target.move_id
            or target.move_id.state != "draft"
            or target.move_id.move_type != "out_invoice"
            or target.move_id.dte_certification_run_line_id != target
        ):
            raise ValidationError(
                _(
                    "The referenced invoice for return case %(case)s is "
                    "unavailable for draft generation.",
                    case=line.case_identifier,
                )
            )
        return target

    def _check_invoice_cancellation_target(self, line, selected_lines):
        target = line.referenced_line_id
        if (
            target
            and target.generation_state == "pending"
            and target not in selected_lines
        ):
            raise ValidationError(
                _(
                    "Select the referenced invoice along with cancellation case "
                    "%(case)s.",
                    case=line.case_identifier,
                )
            )
        if (
            not target
            or target.run_id != line.run_id
            or target.document_code != "33"
            or target.sequence >= line.sequence
            or line.snapshot.get("reference_code") != "1"
            or line.snapshot.get("reference_target_document_code") != "33"
            or not line.snapshot.get("reference_reason")
            or target.generation_state not in {"pending", "generated"}
            or line.snapshot_digest != self._snapshot_digest(line.snapshot)
            or line.source_digest != line.run_id.set_digest
            or target.snapshot_digest != self._snapshot_digest(target.snapshot)
            or target.source_digest != line.run_id.set_digest
        ):
            raise ValidationError(
                _(
                    "Invoice cancellation case %(case)s requires a valid earlier "
                    "type-33 invoice in this execution.",
                    case=line.case_identifier,
                )
            )
        if target.generation_state == "generated" and (
            not target.move_id
            or target.move_id.state != "draft"
            or target.move_id.move_type != "out_invoice"
            or target.move_id.dte_certification_run_line_id != target
            or not target.partner_id
            or target.move_id.partner_id != target.partner_id
        ):
            raise ValidationError(
                _(
                    "The referenced invoice for cancellation case %(case)s is "
                    "unavailable for draft generation.",
                    case=line.case_identifier,
                )
            )
        return target

    def _check_invoice_cancellation_balance(self, line, target, selected_lines):
        adjustments = target.run_id.line_ids.filtered(
            lambda item: (
                item != line
                and item.referenced_line_id == target
                and item.document_code == "61"
                and item.snapshot.get("note_operation") in {"return", "cancel"}
                and (item.generation_state == "generated" or item in selected_lines)
            )
        )
        if adjustments:
            raise ValidationError(
                _(
                    "Invoice cancellation case %(case)s cannot reverse the full "
                    "invoice because another monetary adjustment is generated "
                    "or selected.",
                    case=line.case_identifier,
                )
            )
