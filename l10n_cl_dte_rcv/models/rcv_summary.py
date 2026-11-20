# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class RcvSummary(models.Model):
    """Totals by document type that the SII reports for one state."""

    _name = "l10n_cl.dte.rcv.summary"
    _description = "SII RCV Summary Row"
    _order = "sync_id, sii_state, document_type_code"
    _check_company_auto = True

    sync_id = fields.Many2one(
        "l10n_cl.dte.rcv.sync", required=True, index=True, ondelete="cascade"
    )
    company_id = fields.Many2one(related="sync_id.company_id", store=True, index=True)
    operation = fields.Selection(related="sync_id.operation", store=True)
    sii_state = fields.Char(string="SII State", index=True)
    document_type_code = fields.Char(required=True, index=True)
    document_type_name = fields.Char()
    document_count = fields.Integer()
    net_amount = fields.Monetary(currency_field="currency_id")
    exempt_amount = fields.Monetary(currency_field="currency_id")
    vat_amount = fields.Monetary(string="VAT Amount", currency_field="currency_id")
    vat_not_recoverable = fields.Monetary(
        string="Non-Recoverable VAT", currency_field="currency_id"
    )
    common_vat = fields.Monetary(string="Common Use VAT", currency_field="currency_id")
    total_amount = fields.Monetary(currency_field="currency_id")
    currency_id = fields.Many2one(related="sync_id.company_id.currency_id")
