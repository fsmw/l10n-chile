# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class IncomingDteExchangeLine(models.Model):
    _name = "l10n_cl.incoming.dte.exchange.line"
    _description = "Incoming DTE Exchange Response Detail"
    _order = "exchange_id desc, id"
    _check_company_auto = True

    exchange_id = fields.Many2one(
        "l10n_cl.incoming.dte.exchange",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
    )
    kind = fields.Selection(related="exchange_id.kind", store=True, index=True)
    company_id = fields.Many2one(
        related="exchange_id.company_id", store=True, index=True
    )
    exchange_state = fields.Selection(related="exchange_id.state")
    incoming_document_id = fields.Many2one(
        "l10n_cl.incoming.dte",
        readonly=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    document_code = fields.Char(readonly=True)
    folio = fields.Char(readonly=True)
    issuer_vat = fields.Char(string="Issuer RUT", readonly=True)
    issue_date = fields.Date(readonly=True)
    total_amount = fields.Char(readonly=True)
    status_code = fields.Char(readonly=True)
    gloss = fields.Char(readonly=True)

    def init(self):
        # Idempotency guard: one commercial result and one receipt per DTE.
        self.env.cr.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "l10n_cl_incoming_dte_exchange_line_document_uniq "
            "ON l10n_cl_incoming_dte_exchange_line (incoming_document_id, kind) "
            "WHERE kind IN ('result', 'receipt') "
            "AND incoming_document_id IS NOT NULL"
        )

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super  # noqa: ARG002
        raise UserError(_("Exchange response details are created when signing."))

    @api.model
    def _create_internal(self, vals_list):
        return super(IncomingDteExchangeLine, self.sudo()).create(vals_list)

    def write(self, vals):  # pylint: disable=method-required-super  # noqa: ARG002
        raise UserError(_("Exchange response details are immutable."))

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("Exchange response details must be retained."))
