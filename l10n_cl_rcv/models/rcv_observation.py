# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import fields, models


class RcvObservation(models.Model):
    _name = "l10n.cl.rcv.observation"
    _description = "SII RCV document observation"
    _order = "observed_at desc, id desc"
    _check_company_auto = True

    document_id = fields.Many2one(
        "l10n.cl.rcv.document", index=True, ondelete="set null"
    )
    sync_id = fields.Many2one(
        "l10n.cl.rcv.sync", required=True, index=True, ondelete="cascade"
    )
    company_id = fields.Many2one(related="sync_id.company_id", store=True, index=True)
    observed_at = fields.Datetime(
        required=True, default=fields.Datetime.now, index=True
    )
    payload_hash = fields.Char(required=True, index=True)
    tax_period = fields.Char()
    sii_state = fields.Char()
    net_amount = fields.Monetary(currency_field="currency_id")
    exempt_amount = fields.Monetary(currency_field="currency_id")
    vat_amount = fields.Monetary(currency_field="currency_id")
    vat_not_recoverable = fields.Monetary(
        string="IVA no recuperable", currency_field="currency_id"
    )
    common_vat = fields.Monetary(
        string="IVA de uso común", currency_field="currency_id"
    )
    tax_breakdown_available = fields.Boolean(string="Desglose tributario completo")
    total_amount = fields.Monetary(currency_field="currency_id")
    currency_id = fields.Many2one(related="sync_id.company_id.currency_id")
    source = fields.Selection(related="sync_id.source", store=True)

    _sql_constraints = [
        (
            "observation_hash_uniq",
            "unique(sync_id, payload_hash)",
            "La observación ya existe en esta ejecución.",
        )
    ]
