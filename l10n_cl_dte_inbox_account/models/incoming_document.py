# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models

from ..services.binding_service import IncomingAccountBindingService
from ..services.policy_service import IncomingAccountPolicyService


class IncomingDte(models.Model):
    _inherit = "l10n_cl.incoming.dte"

    account_binding_ids = fields.One2many(
        "l10n_cl.incoming.dte.account.binding",
        "incoming_document_id",
        string="Vendor Bill Links",
        readonly=True,
        groups="l10n_cl_dte_inbox_account.group_inbox_account_user",
    )

    def action_prepare_vendor_bill(self):
        self.ensure_one()
        IncomingAccountPolicyService(self.env).check_access(self)
        if self.account_binding_ids:
            return self.action_open_vendor_bill()
        values = self.env["l10n_cl.incoming.dte.bill.wizard"]._preview_values(
            IncomingAccountBindingService(self.env).prepare(self)
        )
        wizard = self.env["l10n_cl.incoming.dte.bill.wizard"].create(values)
        return dict(
            type="ir.actions.act_window",
            name=self.env._("Prepare Vendor Bill"),
            res_model=wizard._name,
            res_id=wizard.id,
            view_mode="form",
            target="new",
        )

    def action_open_vendor_bill(self):
        self.ensure_one()
        IncomingAccountPolicyService(self.env).check_access(self)
        self.account_binding_ids.ensure_one()
        return self.account_binding_ids.action_open_move()
