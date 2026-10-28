# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    def _l10n_cl_rcv_dte_attachment(self, field_name):
        """Return a DTE attachment owned by the optional DTE localization.

        Without that localization the field does not exist. The XML evidence is
        still attached to the move, so the fallback reads the newest XML
        attachment instead of returning nothing.
        """
        self.ensure_one()
        if field_name in self._fields and self[field_name]:
            return self[field_name]
        return self.env["ir.attachment"].search(
            [
                ("res_model", "=", self._name),
                ("res_id", "=", self.id),
                ("name", "ilike", "%.xml"),
            ],
            limit=1,
            order="id desc",
        )

    l10n_cl_rcv_document_id = fields.Many2one(
        "l10n.cl.rcv.document",
        copy=False,
        index=True,
        ondelete="set null",
        string="Documento RCV de origen",
    )
    _sql_constraints = [
        (
            "rcv_origin_document_uniq",
            "unique(l10n_cl_rcv_document_id)",
            "Un documento RCV solo puede originar una factura.",
        )
    ]
    l10n_cl_rcv_document_ids = fields.One2many(
        "l10n.cl.rcv.document", "move_id", string="RCV documents"
    )
    l10n_cl_rcv_reconciliation_state = fields.Selection(
        [
            ("not_checked", "Sin revisar RCV"),
            ("matched", "Observado en RCV"),
            ("not_in_sii", "No observado en RCV"),
            ("indeterminate", "RCV incompleto"),
        ],
        default="not_checked",
        copy=False,
        index=True,
        string="Conciliación RCV",
    )
    l10n_cl_rcv_sync_id = fields.Many2one(
        "l10n.cl.rcv.sync",
        copy=False,
        index=True,
        check_company=True,
        string="Última revisión RCV",
    )
