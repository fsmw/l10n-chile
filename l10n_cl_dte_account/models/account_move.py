import base64

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.l10n_cl_dte_xml.services.invoice_snapshot import InvoiceSnapshotService

from ..services.dte_issuance_adapter import InvoiceIssuanceAdapter
from ..services.dte_issuance_service import DteIssuanceService


class AccountMove(models.Model):
    _inherit = "account.move"

    dte_document_ids = fields.One2many(
        "l10n_cl.electronic.document", "source_move_id", string="Electronic Documents"
    )
    dte_document_count = fields.Integer(
        string="DTE Documents", compute="_compute_dte_document_count"
    )
    dte_document_type_code = fields.Char(
        related="l10n_latam_document_type_id.code",
        string="DTE Document Type Code",
    )
    dte_issuance_available = fields.Boolean(compute="_compute_dte_issuance_available")

    def issue_dte(self):
        """Issue the selected Chilean DTE through the complete lifecycle."""
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_user"):
            raise AccessError(_("Only a DTE user can issue electronic documents."))
        allowed_types = self._get_dte_allowed_move_types(
            self.l10n_latam_document_type_id.code
            if self.l10n_latam_document_type_id
            else False
        )
        if self.state != "posted" or self.move_type not in allowed_types:
            raise UserError(_("Only posted customer documents can issue a DTE."))
        if not self.l10n_latam_document_type_id:
            raise UserError(_("Select a Chilean DTE document type before issuing."))
        document_code = self.l10n_latam_document_type_id.code
        adapter = self._get_dte_issuance_adapter(document_code)
        return DteIssuanceService().issue(self, adapter, document_code)

    def _get_dte_issuance_adapter(self, document_code):
        self.ensure_one()
        if document_code in InvoiceSnapshotService.SUPPORTED_DOCUMENT_CODES:
            return InvoiceIssuanceAdapter()
        raise UserError(
            _("DTE document type %(code)s is not supported by this issuance flow.")
            % {"code": document_code}
        )

    def _get_dte_allowed_move_types(self, document_code):
        """Return the accounting types supported by the selected DTE adapter."""
        self.ensure_one()
        return ("out_invoice",)

    @api.depends(
        "state", "move_type", "l10n_latam_document_type_id", "dte_document_ids"
    )
    def _compute_dte_issuance_available(self):
        for move in self:
            code = (
                move.l10n_latam_document_type_id.code
                if move.l10n_latam_document_type_id
                else False
            )
            try:
                move._get_dte_issuance_adapter(code)
                supported_types = move._get_dte_allowed_move_types(code)
                move.dte_issuance_available = bool(
                    code
                    and move.state == "posted"
                    and move.move_type in supported_types
                    and not move.dte_document_ids
                )
            except (UserError, ValueError):
                move.dte_issuance_available = False

    @api.depends("dte_document_ids")
    def _compute_dte_document_count(self):
        for move in self:
            move.dte_document_count = len(move.dte_document_ids)

    def action_view_dte_documents(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "l10n_cl_dte_core.action_electronic_document"
        )
        action["domain"] = [("source_move_id", "=", self.id)]
        documents = self.dte_document_ids
        if len(documents) == 1:
            action["views"] = [(False, "form")]
            action["res_id"] = documents.id
        return action

    @api.onchange("l10n_latam_document_type_id")
    def _onchange_l10n_latam_document_type_id(self):
        result = super()._onchange_l10n_latam_document_type_id()
        for move in self.filtered(
            lambda item: (
                item.state == "draft"
                and item.posted_before
                and not item.dte_document_ids
            )
        ):
            move.l10n_latam_document_number = False
            move.name = False
            move._compute_name()
        return result

    def _select_dte_caf(self, environment, document_code):
        self.ensure_one()
        selected_id = self.env.context.get("dte_caf_authorization_id")
        domain = [
            ("company_id", "=", self.company_id.id),
            ("document_code", "=", document_code),
            ("sii_environment", "=", environment),
            ("state", "=", "validated"),
            "|",
            ("date_to", "=", False),
            ("date_to", ">=", fields.Date.context_today(self)),
        ]
        if selected_id:
            domain.append(("id", "=", selected_id))
        authorizations = self.env["l10n_cl.caf.authorization"].search(domain)
        if len(authorizations) != 1:
            raise UserError(
                _(
                    "Select exactly one current validated CAF for DTE type "
                    "%(code)s before issuance."
                )
                % {"code": document_code}
            )
        authorizations._check_caf_integrity()
        return authorizations

    def _create_dte_artifact(
        self, document, role, xml_bytes, producer_version="l10n_cl_dte_account/18.0"
    ):
        return self.env["l10n_cl.electronic.artifact"].create(
            {
                "document_id": document.id,
                "role": role,
                "content": base64.b64encode(xml_bytes),
                "mimetype": "application/xml",
                "encoding": "ISO-8859-1",
                "producer_version": producer_version,
            }
        )
