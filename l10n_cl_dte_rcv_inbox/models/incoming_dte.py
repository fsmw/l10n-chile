# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

from odoo.addons.l10n_cl_dte_inbox_account.services.common import (
    canonical_folio,
    vat,
)

DTE_USER_GROUP = "l10n_cl_dte_core.group_dte_user"


class IncomingDte(models.Model):
    _inherit = "l10n_cl.incoming.dte"

    rcv_document_ids = fields.One2many(
        "l10n_cl.dte.rcv.document",
        "incoming_dte_id",
        string="RCV Documents",
        readonly=True,
        groups=DTE_USER_GROUP,
    )

    @api.model
    def _import_xml(
        self, content, channel="operator_imported", filename=False, validation=None
    ):
        record = super()._import_xml(
            content, channel=channel, filename=filename, validation=validation
        )
        record._link_rcv_documents()
        return record

    def _find_rcv_documents(self):
        """Return the RCV purchases with the fiscal identity of this DTE."""
        self.ensure_one()
        folio = str(self.folio)
        issuer = vat(self.issuer_vat)
        documents = (
            self.env["l10n_cl.dte.rcv.document"]
            .sudo()
            .with_context(active_test=False)
            .search(
                [
                    ("company_id", "=", self.company_id.id),
                    ("operation", "=", "purchase"),
                    ("document_type_code", "=", self.document_code),
                    # Folios may keep leading zeros; compare them canonically.
                    ("folio", "=like", "%" + folio),
                ]
            )
        )
        return documents.filtered(
            lambda document: canonical_folio(document.folio) == folio
            and vat(document.issuer_vat) == issuer
        )

    def _link_rcv_documents(self):
        """Link this DTE to its RCV purchases, in the company environment."""
        for incoming in self:
            if incoming.sii_environment != incoming.company_id.dte_sii_environment:
                continue
            documents = incoming._find_rcv_documents().filtered(
                lambda document, incoming=incoming: document.incoming_dte_id != incoming
            )
            if documents:
                # A derived link stored by whoever registers the DTE.
                documents.write({"incoming_dte_id": incoming.id})
        return True
