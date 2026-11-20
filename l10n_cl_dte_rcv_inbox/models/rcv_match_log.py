# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class RcvMatchLog(models.Model):
    _inherit = "l10n_cl.dte.rcv.match.log"

    event_type = fields.Selection(
        selection_add=[("draft_created", "Draft Created")],
        ondelete={"draft_created": "cascade"},
    )
