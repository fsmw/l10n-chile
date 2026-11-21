# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models
from odoo.exceptions import UserError

from odoo.addons.l10n_cl_dte_inbox_account.services.policy_service import (
    IncomingAccountPolicyService,
)

from ..services.common import PURCHASE_CODE
from ..services.received_service import ReceivedPurchaseService


class IncomingDte(models.Model):
    _inherit = "l10n_cl.incoming.dte"

    def action_prepare_customer_invoice(self):
        """Preview the customer invoice of a DTE 46 our buyer issued to us."""
        self.ensure_one()
        IncomingAccountPolicyService(self.env).check_access(self)
        if self.document_code != PURCHASE_CODE:
            raise UserError(
                self.env._("Only a received purchase invoice (DTE 46) is our sale.")
            )
        if self.account_binding_ids:
            return self.action_open_vendor_bill()
        wizard_model = self.env["l10n_cl.incoming.dte.bill.wizard"]
        wizard = wizard_model.create(
            wizard_model._preview_values(
                ReceivedPurchaseService(self.env).prepare(self)
            )
        )
        return wizard._received46_action()
