# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class IncomingDteImport(models.Model):
    _inherit = "l10n_cl.incoming.dte.import"

    exchange_ids = fields.One2many(
        "l10n_cl.incoming.dte.exchange",
        "import_id",
        string="Reception Responses",
        readonly=True,
    )

    def action_send_reception_response(self):
        """Sign once and email the RecepcionEnvio of this received file."""
        exchanges = self.env["l10n_cl.incoming.dte.exchange"]
        for batch in self:
            exchange = exchanges._prepare_reception(batch)
            exchange._email_pending()
            exchanges |= exchange
        return exchanges.action_open()
