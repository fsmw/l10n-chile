# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import json
from decimal import Decimal
from unittest.mock import patch

from lxml import etree

from odoo import Command

from odoo.addons.l10n_cl_dte_account.services.dte_issuance_service import (
    DteIssuanceService,
)
from odoo.addons.l10n_cl_dte_boleta.services.boleta_snapshot import (
    BoletaSnapshotService,
)
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase


class TestMixedBoletaTotals(DteInvoiceTestCase):
    def test_mixed_accounting_boleta_reconciles_snapshot_and_signed_xml(self):
        invoice = self.make_invoice("39", post=False)
        taxable_line = invoice.invoice_line_ids.ensure_one()
        taxable_line.write(
            {
                "price_unit": 1000,
                "tax_ids": [Command.set(self.tax_19.ids)],
            }
        )
        invoice.write(
            {
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Exempt service",
                            "product_id": self.product_a.id,
                            "quantity": 1,
                            "price_unit": 500,
                            "tax_ids": [Command.clear()],
                        }
                    )
                ]
            }
        )
        invoice.action_post()
        original_totals = (
            invoice.amount_untaxed,
            invoice.amount_tax,
            invoice.amount_total,
        )
        self.assertEqual(original_totals, (1500, 190, 1690))

        authorization = self.validate_authorization(
            document_code="39", folio_start=810, folio_end=815
        )
        self.create_signing_profile()
        adapter = invoice._get_dte_issuance_adapter("39")
        with patch.object(
            BoletaSnapshotService,
            "_annual_135_uf_limit",
            return_value=Decimal("999999999"),
        ):
            document = DteIssuanceService().prepare(
                invoice.with_context(dte_caf_authorization_id=authorization.id),
                adapter,
                "39",
            )

        snapshot_artifact = document.artifact_ids.filtered(
            lambda artifact: artifact.role == "source_snapshot"
        )
        snapshot = json.loads(base64.b64decode(snapshot_artifact.content))
        self.assertEqual(
            snapshot["totals"],
            {
                "net": "1000",
                "exempt": "500",
                "vat_rate": "19",
                "vat": "190",
                "total": "1690",
            },
        )

        signed_artifact = document.artifact_ids.filtered(
            lambda artifact: artifact.role == "signed_xml"
        )
        signed_xml = base64.b64decode(signed_artifact.content, validate=True)
        adapter.validate_document(signed_xml)
        root = etree.fromstring(signed_xml)
        namespace = {"sii": "http://www.sii.cl/SiiDte"}
        self.assertEqual(root.findtext(".//sii:MntNeto", namespaces=namespace), "1000")
        self.assertEqual(root.findtext(".//sii:MntExe", namespaces=namespace), "500")
        self.assertEqual(root.findtext(".//sii:IVA", namespaces=namespace), "190")
        self.assertEqual(root.findtext(".//sii:MntTotal", namespaces=namespace), "1690")
        self.assertEqual(
            invoice.amount_untaxed, original_totals[0], "Issuance changed source net"
        )
        self.assertEqual(
            invoice.amount_tax, original_totals[1], "Issuance changed source VAT"
        )
        self.assertEqual(
            invoice.amount_total, original_totals[2], "Issuance changed source total"
        )
        self.assertEqual(document.preparation_state, "signed")
        self.assertFalse(
            self.env["l10n_cl.electronic.submission"].search(
                [("document_id", "=", document.id)]
            ),
            "prepare() must not enqueue or send the DTE",
        )
