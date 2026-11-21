# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.exceptions import UserError


class IncomingDte(models.Model):
    _inherit = "l10n_cl.incoming.dte"

    exchange_line_ids = fields.One2many(
        "l10n_cl.incoming.dte.exchange.line",
        "incoming_document_id",
        string="Exchange Responses",
        readonly=True,
    )

    def action_open_exchange_wizard(self):
        """Open the commercial result or goods receipt response for these DTEs."""
        kind = self.env.context.get("exchange_kind", "result")
        if any(document.state != "reviewed" for document in self):
            raise UserError(_("Review the incoming DTEs before answering them."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Email Exchange Response"),
            "res_model": "l10n_cl.incoming.dte.exchange.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_kind": kind,
                "default_document_ids": self.ids,
            },
        }

    def action_send_reception_response(self):
        """Answer the reception of the file in which this DTE arrived."""
        self.ensure_one()
        batch = self.sudo().import_line_ids.sorted("id")[:1].import_id
        if not batch:
            raise UserError(_("This DTE was not received in an imported file."))
        return batch.with_env(self.env).action_send_reception_response()
