# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, models

from ..services.common import PURCHASE_CODE
from ..services.received_service import ReceivedPurchaseService


class IncomingDteBillWizard(models.TransientModel):
    _inherit = "l10n_cl.incoming.dte.bill.wizard"

    def _is_received46(self):
        self.ensure_one()
        return self.incoming_document_id.document_code == PURCHASE_CODE

    @api.model
    def default_get(self, field_names):
        source_id = self.env.context.get("default_incoming_document_id")
        incoming = self.env["l10n_cl.incoming.dte"].browse(source_id)
        if not source_id or incoming.document_code != PURCHASE_CODE:
            return super().default_get(field_names)
        values = super(
            IncomingDteBillWizard,
            self.with_context(default_incoming_document_id=False),
        ).default_get(field_names)
        values.update(
            self._preview_values(ReceivedPurchaseService(self.env).prepare(incoming))
        )
        return {key: value for key, value in values.items() if key in field_names}

    def action_refresh_preview(self):
        if not self._is_received46():
            return super().action_refresh_preview()
        self._check_operator()
        preview = ReceivedPurchaseService(self.env).prepare(
            self.incoming_document_id,
            self._prepare_overrides(),
            self.journal_id,
            self.accounting_date,
        )
        self.write(self._preview_values(preview))
        return self._received46_action()

    def action_confirm(self):
        if not self._is_received46():
            return super().action_confirm()
        self._check_operator()
        move = ReceivedPurchaseService(self.env).confirm(self)
        return move.get_formview_action()

    def _received46_action(self):
        self.ensure_one()
        return dict(
            type="ir.actions.act_window",
            name=self.env._("Prepare Customer Invoice"),
            res_model=self._name,
            res_id=self.id,
            view_mode="form",
            views=[
                (
                    self.env.ref(
                        "l10n_cl_dte_purchase_received.incoming_received46_wizard_view_form"
                    ).id,
                    "form",
                )
            ],
            target="new",
        )
