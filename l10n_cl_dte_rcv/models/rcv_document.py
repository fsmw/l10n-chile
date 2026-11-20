# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

from .rcv_period import DOCUMENT_OPERATIONS, RCV_SOURCES


class RcvDocument(models.Model):
    """A fiscal document as registered in the SII RCV.

    The fiscal identity is company, operation, document type, issuer RUT and
    folio. Each read updates the document and adds an observation.
    """

    _name = "l10n_cl.dte.rcv.document"
    _description = "SII RCV Document"
    _order = "tax_period desc, document_date desc, folio desc"
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True)
    company_id = fields.Many2one(
        "res.company", required=True, index=True, ondelete="cascade"
    )
    operation = fields.Selection(DOCUMENT_OPERATIONS, required=True, index=True)
    source = fields.Selection(RCV_SOURCES, required=True, default="sii")
    tax_period = fields.Char(required=True, index=True, help="YYYYMM")
    document_type_code = fields.Char(required=True, index=True)
    document_type_name = fields.Char()
    issuer_vat = fields.Char(string="Issuer RUT", required=True, index=True)
    issuer_name = fields.Char()
    counterpart_vat = fields.Char(string="Counterpart RUT", index=True)
    counterpart_name = fields.Char()
    folio = fields.Char(required=True, index=True)
    document_date = fields.Date()
    accounting_date = fields.Date()
    sii_state = fields.Char(string="SII State", index=True)
    net_amount = fields.Monetary(currency_field="currency_id")
    exempt_amount = fields.Monetary(currency_field="currency_id")
    vat_amount = fields.Monetary(string="VAT Amount", currency_field="currency_id")
    vat_not_recoverable = fields.Monetary(
        string="Non-Recoverable VAT", currency_field="currency_id"
    )
    common_vat = fields.Monetary(string="Common Use VAT", currency_field="currency_id")
    tax_breakdown_available = fields.Boolean(
        string="Complete Tax Breakdown",
        copy=False,
        help="The read reported recoverable, non-recoverable and common use VAT.",
    )
    total_amount = fields.Monetary(currency_field="currency_id")
    currency_id = fields.Many2one(related="company_id.currency_id", store=True)
    active = fields.Boolean(default=True, index=True)
    last_sync_id = fields.Many2one(
        "l10n_cl.dte.rcv.sync",
        string="Last Read",
        index=True,
        ondelete="set null",
        check_company=True,
    )
    observation_ids = fields.One2many("l10n_cl.dte.rcv.observation", "document_id")

    _sql_constraints = [
        (
            "fiscal_identity_uniq",
            "unique(company_id, operation, document_type_code, issuer_vat, folio)",
            "This fiscal document is already registered.",
        ),
    ]

    @api.depends("document_type_code", "folio", "issuer_vat")
    def _compute_name(self):
        for document in self:
            document.name = "{}-{} · {}".format(
                document.document_type_code or "",
                document.folio or "",
                document.issuer_vat or "",
            )
