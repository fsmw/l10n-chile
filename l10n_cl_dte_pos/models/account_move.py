# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, models
from odoo.exceptions import AccessError


class AccountMove(models.Model):
    _inherit = "account.move"

    def _check_dte_source_access(self, document_code):
        self.ensure_one()
        self.check_access("read")
        if self.browse().has_access("write") or document_code not in {"39", "41", "61"}:
            return super()._check_dte_source_access(document_code)
        # Waive only the model ACL, never a deployment's write record rules.
        domain = self.env["ir.rule"]._compute_domain(self._name, "write")
        if self - self.sudo().filtered_domain(domain):
            raise AccessError(_("The accounting source is restricted for issuance."))
        orders = (
            self.env["pos.order"]
            .sudo()
            .search([("account_move", "=", self.id)], limit=2)
        )
        order = orders.with_user(self.env.user)
        if len(order) != 1:
            raise AccessError(_("Use one native POS invoice source."))
        order.check_access("read")
        order.check_access("write")
        session = order.session_id
        session.check_access("read")
        session.check_access("write")
        if not (
            self.env.user.has_group("point_of_sale.group_pos_user")
            and self.env.user.has_group("l10n_cl_dte_core.group_dte_user")
            and session.state == "opened"
            and session.dte_pos_accounting_mode == "per_order_invoice"
            and self.company_id == order.company_id == session.company_id
            and self.company_id in self.env.companies
            and self.company_id.country_id.code == "CL"
            and self.state == "posted"
            and order.state == "invoiced"
            and self.l10n_latam_document_type_id.code == document_code
            and self.currency_id == order.currency_id
            and self.partner_id.id
            == order.partner_id.address_get(["invoice"])["invoice"]
            and self.currency_id.is_zero(order.amount_paid - order.amount_total)
            and self.currency_id.is_zero(self.amount_total - abs(order.amount_total))
        ):
            raise AccessError(
                _("The native POS invoice is not authorized for issuance.")
            )
        order._check_dte_invoice_source(self, document_code)
