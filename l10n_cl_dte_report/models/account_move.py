# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo import models

from ..report.dte_document_report import render_ted_barcode
from .report_sample import _extract_ted_bytes


class AccountMove(models.Model):
    _inherit = "account.move"

    def _get_dte_print_document(self):
        """Return the issued DTE whose signed TED this invoice must print."""
        self.ensure_one()
        documents = self.sudo().dte_document_ids.filtered(
            lambda document: document.fiscal_state != "rejected"
            and document.artifact_ids.filtered(
                lambda artifact: artifact.role == "signed_xml"
            )
        )
        return documents.sorted("id")[-1:]

    def _get_dte_print_values(self):
        """Values for the SII stamp block; empty when no DTE was signed."""
        self.ensure_one()
        document = self._get_dte_print_document()
        if not document:
            return {}
        artifact = document._get_signed_artifact()
        artifact.action_verify_integrity()
        signed = base64.b64decode(
            artifact.with_context(bin_size=False).content, validate=True
        )
        company = self.company_id
        resolution_date = company.dte_sii_resolution_date
        return {
            "folio": document.folio,
            "barcode": render_ted_barcode(_extract_ted_bytes(signed)),
            "resolution_number": company.dte_sii_resolution_number or "0",
            "resolution_year": resolution_date.year if resolution_date else "",
            "is_demo": document.sii_environment == "demo",
        }
