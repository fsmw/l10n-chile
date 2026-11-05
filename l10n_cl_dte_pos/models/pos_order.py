# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.l10n_cl_dte_account.services.dte_issuance_service import (
    DteIssuanceService,
)

from ..services.pos_boleta_issuance_adapter import PosBoletaIssuanceAdapter


class PosOrder(models.Model):
    _inherit = "pos.order"

    @api.model
    def _process_order(self, order, existing_order):
        session = self.env["pos.session"].browse(order.get("session_id")).exists()
        if session and session.company_id.country_id.code == "CL":
            mode = session.dte_pos_accounting_mode or (
                session.company_id.dte_pos_accounting_mode
            )
            if mode == "per_order_invoice":
                order["to_invoice"] = True
            elif mode == "session_summary":
                order["to_invoice"] = False
        return super()._process_order(order, existing_order)

    dte_document_ids = fields.One2many(
        comodel_name="l10n_cl.electronic.document",
        inverse_name="source_pos_order_id",
        string="DTE Documents",
        readonly=True,
    )
    dte_related_document_ids = fields.Many2many(
        comodel_name="l10n_cl.electronic.document",
        string="Related DTE Documents",
        compute="_compute_dte_related_documents",
        readonly=True,
    )
    dte_document_count = fields.Integer(
        string="DTE Document Count",
        compute="_compute_dte_document_count",
    )
    dte_document_code = fields.Selection(
        selection=[("39", "Boleta electrónica"), ("41", "Boleta exenta electrónica")],
        string="DTE Type",
        compute="_compute_dte_document_code",
        readonly=True,
    )
    dte_issuance_state = fields.Selection(
        selection=[
            ("not_issued", "Not issued"),
            ("queued", "Queued"),
            ("processing", "Processing"),
            ("accepted", "Accepted"),
            ("rejected", "Rejected"),
            ("unknown", "Unknown"),
            ("review_required", "Review required"),
        ],
        string="DTE Status",
        compute="_compute_dte_issuance_state",
        readonly=True,
    )

    @api.depends("dte_document_ids", "account_move.dte_document_ids")
    def _compute_dte_related_documents(self):
        for order in self:
            order.dte_related_document_ids = order._get_pos_dte_documents()

    @api.depends("dte_related_document_ids")
    def _compute_dte_document_count(self):
        """Count the electronic documents linked to this order."""
        for order in self:
            order.dte_document_count = len(order.dte_related_document_ids)

    @api.depends("lines.tax_ids", "state", "account_move.l10n_latam_document_type_id")
    def _compute_dte_document_code(self):
        """Select boleta type from the effective taxes on POS lines."""
        from odoo.addons.l10n_cl_dte_xml.services.invoice_snapshot import (
            InvoiceSnapshotService,
        )

        classifier = InvoiceSnapshotService()
        for order in self:
            if order.account_move and order.account_move.l10n_latam_document_type_id:
                code = order.account_move.l10n_latam_document_type_id.code
                order.dte_document_code = code if code in {"39", "41"} else False
                continue
            lines = order.lines.filtered(lambda line: line.product_id and line.qty > 0)
            taxes = lines.mapped("tax_ids")
            taxable = bool(taxes) and classifier._is_taxable_19_percent(taxes)
            order.dte_document_code = "39" if taxable else ("41" if lines else False)

    @api.depends("dte_related_document_ids")
    def _compute_dte_issuance_state(self):
        """Expose a summary status without replacing DTE lifecycle states."""
        state_map = {
            "submitted": "processing",
            "processing": "processing",
            "accepted": "accepted",
            "rejected": "rejected",
            "outcome_unknown": "unknown",
        }
        for order in self:
            documents = order.dte_related_document_ids
            if not documents:
                order.dte_issuance_state = "not_issued"
            elif len(documents) != 1 or documents.preparation_state != "queued":
                order.dte_issuance_state = "review_required"
            else:
                order.dte_issuance_state = state_map.get(
                    documents.fiscal_state, "queued"
                )

    def _process_saved_order(self, draft):
        """Issue a POS-sourced boleta after a paid order is persisted."""
        result = super()._process_saved_order(draft)
        if not draft and self.state in {"paid", "invoiced"}:
            self._issue_pos_boleta_if_required()
        return result

    def _get_pos_dte_documents(self):
        self.ensure_one()
        documents = self.dte_document_ids
        if self.account_move:
            documents |= self.account_move.dte_document_ids
        return documents

    def _issue_pos_boleta_if_required(self):
        self.ensure_one()
        if self.company_id.country_id.code != "CL":
            return self.env["l10n_cl.electronic.document"]
        code = self.dte_document_code
        if code not in {"39", "41"}:
            raise UserError(_("Only Chilean boletas 39 and 41 are supported."))
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_user"):
            raise AccessError(_("The POS operator needs the DTE User group."))
        if self.session_id.dte_pos_accounting_mode == "per_order_invoice":
            invoice = self.account_move
            if not invoice or invoice.state != "posted":
                raise UserError(_("The POS order does not have its posted invoice."))
            if not invoice.l10n_latam_document_type_id or (
                invoice.l10n_latam_document_type_id.code != code
            ):
                raise UserError(
                    _("The POS invoice type does not match its effective taxes.")
                )
            if invoice.dte_document_ids:
                return invoice.dte_document_ids
            return DteIssuanceService().issue(
                invoice, invoice._get_dte_issuance_adapter(code), code
            )
        if self.session_id.dte_pos_accounting_mode != "session_summary":
            raise UserError(_("The POS session has an unsupported DTE mode."))
        if self.state != "paid":
            raise UserError(_("A POS order must be paid before DTE issuance."))
        if self.dte_document_ids:
            return self.dte_document_ids
        return DteIssuanceService().issue(self, PosBoletaIssuanceAdapter(), code)

    def action_view_dte_documents(self):
        """Open DTEs linked directly or through this order's invoice."""
        self.ensure_one()
        documents = self._get_pos_dte_documents()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "l10n_cl_dte_core.action_electronic_document"
        )
        action["domain"] = [("id", "in", documents.ids)]
        if len(documents) == 1:
            action["views"] = [(False, "form")]
            action["res_id"] = documents.id
        return action

    def _prepare_invoice_vals(self):
        vals = super()._prepare_invoice_vals()
        if self.company_id.country_id.code != "CL":
            return vals
        code = self.dte_document_code
        if code not in {"39", "41"}:
            raise UserError(_("Only Chilean boletas 39 and 41 are supported."))
        document_type = self.env["l10n_latam.document.type"].search(
            [
                ("country_id", "=", self.company_id.country_id.id),
                ("code", "=", code),
            ],
            limit=1,
        )
        if not document_type:
            raise UserError(_("Configure Chilean DTE type %s first.") % code)
        vals["l10n_latam_document_type_id"] = document_type.id
        payments = self.payment_ids.filtered(
            lambda payment: payment.amount > 0 and not payment.is_change
        )
        payment_codes = payments.mapped(
            "payment_method_id.l10n_cl_dte_boleta_payment_method"
        )
        unique_codes = set(payment_codes) - {False}
        if payments and all(payment_codes) and len(unique_codes) == 1:
            vals["dte_boleta_payment_method"] = unique_codes.pop()
        return vals
