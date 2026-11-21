# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..services.note_issuance_adapter import NoteIssuanceAdapter
from ..services.note_reference_service import NoteReferenceService
from ..services.note_snapshot import NoteSnapshotService


class AccountMove(models.Model):
    _inherit = "account.move"

    dte_note_target_document_id = fields.Many2one(
        "l10n_cl.electronic.document",
        string="Referenced DTE",
        index=True,
        copy=False,
        check_company=True,
        ondelete="restrict",
    )
    dte_note_operation = fields.Selection(
        [
            ("cancel", "Cancel document"),
            ("return", "Return goods"),
            ("text", "Correct text"),
            ("amount", "Correct amounts"),
        ],
        string="DTE Note Operation",
        copy=False,
    )
    dte_note_reason = fields.Char(string="DTE Reference Reason", copy=False)
    dte_note_target_move_id = fields.Many2one(
        related="dte_note_target_document_id.source_move_id",
        store=True,
        index=True,
        string="Referenced Accounting Document",
    )
    dte_note_target_folio = fields.Integer(
        related="dte_note_target_document_id.folio", string="Referenced Fiscal Folio"
    )
    dte_note_move_ids = fields.One2many(
        "account.move",
        "dte_note_target_move_id",
        string="Related DTE Notes",
        readonly=True,
    )
    dte_note_count = fields.Integer(
        compute="_compute_dte_note_count", string="DTE Notes"
    )

    @api.depends("dte_note_move_ids")
    def _compute_dte_note_count(self):
        for move in self:
            move.dte_note_count = len(move.dte_note_move_ids)

    def _compute_l10n_latam_document_type(self):
        """Keep the fiscal type of a DTE note bound to its referenced DTE.

        The native debit wizard of l10n_latam recomputes the document type
        after creating the draft and would replace type 56 by the first
        available debit type; the note type is fixed by its fiscal reference.
        """
        notes = self.filtered(
            lambda move: move.dte_note_target_document_id
            and move.l10n_latam_document_type_id.code in {"61", "56"}
        )
        return super(AccountMove, self - notes)._compute_l10n_latam_document_type()

    def _get_dte_issuance_adapter(self, document_code):
        self.ensure_one()
        if str(document_code) in NoteIssuanceAdapter.supported_document_codes:
            return NoteIssuanceAdapter()
        return super()._get_dte_issuance_adapter(document_code)

    def _get_dte_allowed_move_types(self, document_code):
        self.ensure_one()
        if str(document_code) == "61":
            return ("out_refund",) if self.dte_note_target_document_id else ()
        if str(document_code) == "56":
            return ("out_invoice",) if self.dte_note_target_document_id else ()
        return super()._get_dte_allowed_move_types(document_code)

    @api.depends(
        "state",
        "move_type",
        "l10n_latam_document_type_id",
        "dte_document_ids",
        "dte_note_target_document_id",
        "dte_note_operation",
    )
    def _compute_dte_issuance_available(self):
        result = super()._compute_dte_issuance_available()
        for move in self.filtered(
            lambda record: record.dte_document_type_code in {"61", "56"}
        ):
            target = move.dte_note_target_document_id
            move.dte_issuance_available = bool(
                target
                and target.fiscal_state == "accepted"
                and NoteReferenceService.is_ordinary_environment(
                    target, move.company_id
                )
                and move.state == "posted"
                and move.dte_note_operation
                and move.move_type
                in move._get_dte_allowed_move_types(move.dte_document_type_code)
                and not move.dte_document_ids
            )
        return result

    def _validate_dte_note_source(self):
        self.ensure_one()
        return NoteSnapshotService().validate_note_source(
            self, self.l10n_latam_document_type_id.code
        )

    def _prepare_dte_note_values(self, target_document, operation, reason):
        self.ensure_one()
        return {
            "dte_note_target_document_id": target_document.id,
            "dte_note_operation": operation,
            "dte_note_reason": reason,
        }

    def _get_dte_adjustment_action(self, kind):
        """Extension point for an authorized export adjustment modal."""
        self.ensure_one()
        return False

    def _dispatch_dte_adjustment(self, kind, is_modify=False):
        """Route export sources before any domestic or native note creation."""
        self.check_access("read")
        if not self:
            raise UserError(_("Select at least one accessible accounting source."))
        documents = self._get_dte_source_documents()
        export_codes = {"110", "111", "112"}
        exports = (
            self.filtered(lambda move: move.dte_document_type_code in export_codes)
            | documents.filtered(
                lambda doc: doc.document_code in export_codes
            ).source_move_id
        )
        if not exports:
            return False
        if len(self) != 1 or len(documents) > 1:
            raise UserError(
                _("Select one export invoice without mixed or multiple roots.")
            )
        if (
            self.dte_document_type_code != "110"
            or any(doc.document_code != "110" for doc in documents)
            or self.move_type != "out_invoice"
            or self.state != "posted"
        ):
            raise UserError(
                _("An export adjustment requires a posted type 110 invoice.")
            )
        if is_modify:
            raise UserError(
                _("The replacement-invoice workflow is not supported for export DTEs.")
            )
        self.check_access("write")
        action = self._get_dte_adjustment_action(kind)
        if not action:
            raise UserError(
                _("Export adjustments are not available in this installation.")
            )
        return action

    def _dte_document_type_for_code(self, code):
        """Find the localized Chilean document type used by a note wizard."""
        self.ensure_one()
        doc_type = self.env["l10n_latam.document.type"].search(
            [
                ("country_id", "=", self.company_id.country_id.id),
                ("code", "=", str(code)),
            ],
            limit=1,
        )
        if not doc_type:
            raise UserError(
                _("The Chilean document type %(code)s is not configured.")
                % {"code": code}
            )
        return doc_type

    @api.constrains(
        "dte_note_target_document_id",
        "dte_note_operation",
        "dte_note_reason",
        "l10n_latam_document_type_id",
        "reversed_entry_id",
        "debit_origin_id",
        "state",
    )
    def _check_dte_note_metadata(self):
        for move in self:
            code = (
                str(move.l10n_latam_document_type_id.code or "")
                if move.l10n_latam_document_type_id
                else ""
            )
            if (
                move.state == "posted"
                and code in {"61", "56"}
                and not move.dte_note_target_document_id
            ):
                raise ValidationError(
                    _(
                        "A posted DTE note must reference the accepted electronic "
                        "document it adjusts."
                    )
                )
            if not move.dte_note_target_document_id:
                if move.dte_note_operation or move.dte_note_reason:
                    raise ValidationError(
                        _("A DTE note operation and reason require a referenced DTE.")
                    )
                continue
            if not move.l10n_latam_document_type_id or str(
                move.l10n_latam_document_type_id.code
            ) not in {"61", "56"}:
                raise ValidationError(
                    _("Only document types 61 and 56 can reference a DTE here.")
                )
            if (
                code == "56"
                and move.dte_note_operation
                not in {"cancel", "return", "text", "amount"}
            ) or (
                code == "61"
                and move.dte_note_operation
                not in {"cancel", "return", "text", "amount"}
            ):
                raise ValidationError(
                    _("The selected operation is not available for this DTE note type.")
                )
            if move.state == "posted":
                reason = (move.dte_note_reason or "").strip()
                if not reason or len(reason) > 90:
                    raise ValidationError(
                        _(
                            "A posted DTE note requires a reference reason of 1 to "
                            "90 characters."
                        )
                    )
                NoteReferenceService(self.env).validate_reference(
                    move, move.dte_note_target_document_id
                )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            target_id = vals.get("dte_note_target_document_id")
            if target_id:
                target = (
                    self.env["l10n_cl.electronic.document"].browse(target_id).exists()
                )
                if len(target) != 1 or target.company_id.id != vals.get(
                    "company_id", self.env.company.id
                ):
                    raise ValidationError(
                        _("The referenced DTE must belong to the note company.")
                    )
        return super().create(vals_list)

    def _dte_protected_fields(self):
        return super()._dte_protected_fields() | {
            "dte_note_target_document_id",
            "dte_note_operation",
            "dte_note_reason",
            "reversed_entry_id",
            "debit_origin_id",
        }

    def action_view_dte_notes(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "l10n_cl_dte_core.action_electronic_document"
        )
        action["domain"] = [("source_move_id", "in", self.dte_note_move_ids.ids)]
        return action
