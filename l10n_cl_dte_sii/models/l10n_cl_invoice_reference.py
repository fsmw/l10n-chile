# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class L10nClAccountInvoiceReference(models.Model):
    _name = "l10n_cl.account.invoice.reference"
    _description = "Chilean DTE Reference"
    _order = "id"

    move_id = fields.Many2one(
        "account.move", required=True, ondelete="cascade", index=True
    )
    l10n_cl_reference_doc_type_id = fields.Many2one(
        "l10n_latam.document.type",
        string="Tipo de documento de referencia",
        required=True,
    )
    origin_doc_number = fields.Char(string="Folio de referencia", required=True)
    date = fields.Date(string="Fecha de referencia")
    reference_doc_code = fields.Selection(
        [("1", "Annul"), ("2", "Correct text"), ("3", "Correct amounts")],
        string="Código de referencia",
    )
    reason = fields.Char(string="Motivo de referencia")
