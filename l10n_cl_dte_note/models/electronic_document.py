# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import SQL


class ElectronicDocument(models.Model):
    _inherit = "l10n_cl.electronic.document"

    note_target_document_id = fields.Many2one(
        "l10n_cl.electronic.document",
        string="Note Target DTE",
        index=True,
        readonly=True,
        copy=False,
        check_company=True,
        ondelete="restrict",
    )
    note_root_document_id = fields.Many2one(
        "l10n_cl.electronic.document",
        string="Adjustment Root DTE",
        index=True,
        readonly=True,
        copy=False,
        check_company=True,
        ondelete="restrict",
    )
    note_document_ids = fields.One2many(
        "l10n_cl.electronic.document",
        "note_target_document_id",
        string="Referencing Notes",
        readonly=True,
    )
    note_revision = fields.Integer(default=0, readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        """Validate note provenance when the immutable evidence row is created."""
        documents = self.env["l10n_cl.electronic.document"]
        for vals in vals_list:
            target_id, root_id = (
                vals.get("note_target_document_id"),
                vals.get("note_root_document_id"),
            )
            if not target_id and not root_id:
                continue
            target, root = (
                documents.browse(target_id).exists(),
                documents.browse(root_id).exists(),
            )
            if len(target) != 1 or len(root) != 1:
                raise ValidationError(
                    _("A DTE note needs one fiscal target, root and accounting source.")
                )
            code = str(vals.get("document_code"))
            if code not in {"61", "56"}:
                raise ValidationError(
                    _("Only DTE types 61 and 56 can carry note references.")
                )
            if (
                target.preparation_state != "queued"
                or target.fiscal_state != "accepted"
            ):
                raise ValidationError(
                    _("A DTE note must reference a durably queued and accepted source.")
                )
            if target.sii_environment != "demo":
                raise ValidationError(
                    _("DTE notes are currently restricted to Demo evidence.")
                )
            if target.company_id != root.company_id or root.company_id.id != vals.get(
                "company_id"
            ):
                raise ValidationError(
                    _("The DTE note target, root and company must match.")
                )
            if (
                target.sii_environment != vals.get("sii_environment")
                or root.sii_environment != target.sii_environment
            ):
                raise ValidationError(
                    _("The DTE note environment must match its fiscal chain.")
                )
            if root != (target.note_root_document_id or target):
                raise ValidationError(
                    _("The DTE note root does not match its reference chain.")
                )
            if code == "56" and target.document_code not in {"33", "34", "61"}:
                raise ValidationError(
                    _("A debit note can reference only an invoice or credit note.")
                )
            if code == "61" and target.document_code not in {"33", "34", "39", "41"}:
                raise ValidationError(
                    _("A credit note can reference only an original invoice or boleta.")
                )
            self._validate_note_source_values(vals, target, root)
        return super().create(vals_list)

    def write(self, vals):
        """Keep note provenance and revision immutable after creation."""
        if {
            "note_target_document_id",
            "note_root_document_id",
            "note_revision",
        }.intersection(vals):
            raise UserError(
                _("DTE note provenance is immutable after document creation.")
            )
        return super().write(vals)

    @api.constrains(
        "note_target_document_id",
        "note_root_document_id",
        "company_id",
        "sii_environment",
    )
    def _check_note_reference(self):
        """Enforce same-company and one-hop root consistency for note evidence."""
        for document in self.filtered("note_target_document_id"):
            expected_root = (
                document.note_target_document_id.note_root_document_id
                or document.note_target_document_id
            )
            if document.note_root_document_id != expected_root:
                raise ValidationError(
                    _("The DTE note root is inconsistent with its referenced document.")
                )
            if (
                document.company_id != expected_root.company_id
                or document.sii_environment != expected_root.sii_environment
            ):
                raise ValidationError(
                    _("The DTE note company and environment must match its root.")
                )

    def _lock_note_chain(self, target_document, source=None):
        """Lock root and target in deterministic order and bump the root revision."""
        target_document.ensure_one()
        self._check_note_chain_operator(target_document, source=source)
        root = target_document.note_root_document_id or target_document
        if (
            root.company_id not in self.env.companies
            or target_document.company_id not in self.env.companies
        ):
            raise AccessError(
                _("You cannot adjust a DTE outside your allowed companies.")
            )
        ids = sorted({root.id, target_document.id, *self.ids})
        self.env.cr.execute(
            SQL(
                "SELECT id FROM l10n_cl_electronic_document "
                "WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
                ids,
            )
        )
        self.env.cr.execute(
            SQL(
                "UPDATE l10n_cl_electronic_document "
                "SET note_revision = note_revision + 1 "
                "WHERE id = %s RETURNING note_revision",
                root.id,
            )
        )
        self.env.cr.fetchone()
        root.invalidate_recordset(["note_revision"])
        return root

    def action_record_transition(
        self,
        target_state,
        event_code,
        message=None,
        correlation_key=None,
        artifact_id=False,
        folio_number=False,
    ):
        """Serialize accepted/rejected note responses with adjustment issuance."""
        fiscal_states = {
            "submitted",
            "processing",
            "accepted",
            "rejected",
            "outcome_unknown",
        }
        if target_state in fiscal_states and self.note_target_document_id:
            self._lock_note_chain(self.note_target_document_id)
        return super().action_record_transition(
            target_state,
            event_code,
            message=message,
            correlation_key=correlation_key,
            artifact_id=artifact_id,
            folio_number=folio_number,
        )

    @api.model
    def _validate_note_source_values(self, vals, target, root):
        source = self.env["account.move"].browse(vals.get("source_move_id")).exists()
        if len(source) != 1:
            raise ValidationError(_("A DTE note needs one posted accounting source."))
        code = str(vals.get("document_code"))
        if source.dte_note_target_document_id != target or source.state != "posted":
            raise ValidationError(
                _(
                    "The DTE evidence source must be the posted accounting "
                    "note for its target."
                )
            )
        if source.l10n_latam_document_type_id.code != code:
            raise ValidationError(
                _("The accounting note type and fiscal evidence type differ.")
            )
        if (
            code == "61"
            and source.dte_note_operation not in {"cancel", "return", "amount"}
        ) or (code == "56" and source.dte_note_operation not in {"cancel", "amount"}):
            raise ValidationError(
                _("The accounting note operation is not valid for its type.")
            )
        if (
            code == "61"
            and (
                source.move_type != "out_refund"
                or source.reversed_entry_id != target.source_move_id
            )
        ) or (
            code == "56"
            and (
                source.move_type != "out_invoice"
                or source.debit_origin_id != target.source_move_id
            )
        ):
            raise ValidationError(
                _("The accounting note origin does not match its fiscal target.")
            )

    def _check_note_chain_operator(self, target_document, source=None):
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(
                _("Only a DTE manager can serialize DTE note adjustments.")
            )
        target_document.check_access("read")
        target_document.check_access("write")
        root = target_document.note_root_document_id or target_document
        root.check_access("read")
        root.check_access("write")
