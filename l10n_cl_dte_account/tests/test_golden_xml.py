# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Golden-file tests for invoices 33 and 34.

Each test builds a posted invoice, issues it through the real flow (snapshot,
folio, TED, XML, signature) and compares the signed XML with a reviewed file in
``tests/golden/``. See ``l10n_cl_dte_xml/tests/golden.py`` for masking and
regeneration.
"""

import base64
from pathlib import Path
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_xml.services.invoice_snapshot import (
    InvoiceSnapshotService,
)
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase
from odoo.addons.l10n_cl_dte_xml.tests.golden import (
    FIXED_TIMESTAMP,
    assert_golden_xml,
)

GOLDEN_DIR = Path(__file__).parent / "golden"


@tagged("post_install", "-at_install")
class TestGoldenXml(DteInvoiceTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # SII requires GiroRecep on 33, 34 and 56; the generator emits it
        # from the receiver's activity description.
        cls.receiver.sudo().l10n_cl_activity_description = "Comercio al por menor"

    def issue(self, invoice, code, folio):
        self.validate_authorization(
            document_code=code, folio_start=folio, folio_end=folio + 5
        )
        self.create_signing_profile()
        with patch.object(
            InvoiceSnapshotService, "_chile_timestamp", return_value=FIXED_TIMESTAMP
        ):
            document = (
                self.env["account.move"]
                .with_user(self.dte_manager)
                .browse(invoice.id)
                .issue_dte()
            )
        return base64.b64decode(document._get_signed_artifact().content)

    def make_posted_invoice(self, code, lines, taxes):
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.receiver.id,
                "invoice_date": fields.Date.from_string("2026-01-15"),
                "l10n_latam_document_type_id": self.document_types[code].id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "product_id": self.env["product.product"]
                            .sudo()
                            .create({"name": name, "list_price": price})
                            .id,
                            "quantity": quantity,
                            "price_unit": price,
                            "discount": discount,
                            "tax_ids": [Command.set(taxes.ids)],
                        }
                    )
                    for name, quantity, price, discount in lines
                ],
            }
        )
        invoice.action_post()
        return invoice

    def test_invoice_33_two_lines_with_discount(self):
        invoice = self.make_posted_invoice(
            "33",
            [
                ("Arriendo de equipo", 3, 12500, 0),
                ("Servicio de soporte", 1, 40000, 10),
            ],
            self.tax_19,
        )
        assert_golden_xml(
            self, self.issue(invoice, "33", 700), GOLDEN_DIR / "dte_33_basic.xml"
        )

    def test_exempt_invoice_34_two_lines_with_discount(self):
        invoice = self.make_posted_invoice(
            "34",
            [
                ("Asesoria educacional", 2, 30000, 0),
                ("Curso de capacitacion", 1, 50000, 20),
            ],
            self.env["account.tax"],
        )
        assert_golden_xml(
            self, self.issue(invoice, "34", 710), GOLDEN_DIR / "dte_34_exempt.xml"
        )
