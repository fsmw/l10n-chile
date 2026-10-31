# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import io
from unittest.mock import patch

import pdf417gen
from lxml import etree

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from ..services.pdf417 import render_pdf417, render_ted_timbre

TED_XML = b"""<?xml version="1.0" encoding="ISO-8859-1"?>
<TED xmlns="http://www.sii.cl/SiiDte" version="1.0">
  <DD>
    <RE>76543210-3</RE>
    <TD>33</TD>
    <F>63</F>
    <FE>2026-10-30</FE>
    <RR>60803000-K</RR>
    <RSR>Receptor de prueba</RSR>
    <MNT>1000</MNT>
    <IT1>Servicio de prueba</IT1>
    <CAF version="1.0">
      <DA>
        <RE>76543210-3</RE>
        <RS>Emisor de prueba</RS>
        <TD>33</TD>
        <RNG><D>1</D><H>100</H></RNG>
        <FA>2026-01-01</FA>
        <RSAPK><M>abc</M><E>AQAB</E></RSAPK>
        <IDK>1</IDK>
      </DA>
      <FRMA algoritmo="SHA1withRSA">abc</FRMA>
    </CAF>
    <TSTED>2026-10-30T12:00:00</TSTED>
  </DD>
  <FRMT algoritmo="SHA1withRSA">abc</FRMT>
</TED>
"""


@tagged("post_install", "-at_install")
class TestPdf417Service(TransactionCase):
    @staticmethod
    def _ted(markup=TED_XML):
        return etree.fromstring(markup)

    @staticmethod
    def _png(payload, scale):
        image = pdf417gen.render_image(
            pdf417gen.encode(payload, security_level=5, columns=13),
            padding=15,
            scale=scale,
        )
        output = io.BytesIO()
        image.save(output, "PNG")
        return base64.b64encode(output.getvalue()).decode("ascii")

    def test_render_ted_timbre_returns_a_png_and_is_deterministic(self):
        first = render_ted_timbre(self._ted())
        second = render_ted_timbre(self._ted())
        self.assertTrue(first)
        self.assertEqual(first, second)
        self.assertTrue(base64.b64decode(first).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_render_ted_timbre_keeps_the_legacy_bytes(self):
        """The old POS/exports block must stay byte-identical after the refactor."""

        def legacy(scale):
            ted = self._ted()
            for node in ted.iter():
                node.tag = etree.QName(node).localname
            etree.cleanup_namespaces(ted)
            payload = etree.tostring(ted, encoding="ISO-8859-1", xml_declaration=False)
            return self._png(payload, scale)

        for scale in (1, 2):
            with self.subTest(scale=scale):
                self.assertEqual(
                    render_ted_timbre(self._ted(), scale=scale), legacy(scale)
                )

    def test_render_pdf417_keeps_the_delivery_guide_payload_bytes(self):
        """The guide stores the TED as text and encodes that exact payload."""
        payload = etree.tostring(self._ted(), encoding="unicode")
        self.assertEqual(render_pdf417(payload), self._png(payload, 1))

    def test_render_ted_timbre_requires_pdf417gen(self):
        with patch("odoo.addons.l10n_cl_dte_sii.services.pdf417.pdf417gen", None):
            with self.assertRaisesRegex(UserError, "pdf417gen"):
                render_ted_timbre(self._ted())


@tagged("post_install", "-at_install")
class TestDteInvoiceReport(AccountTestInvoicingCommon):
    @classmethod
    @AccountTestInvoicingCommon.setup_country("cl")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.company.sudo().write(
            {
                "l10n_cl_dte_resolution_number": "9999",
                "l10n_cl_dte_resolution_date": "2020-01-01",
            }
        )
        cls.partner = cls.partner_a
        cls.partner.write(
            {
                "country_id": cls.company.country_id.id,
                "vat": "60803000-K",
                "l10n_cl_sii_taxpayer_type": "1",
            }
        )
        cls.journal = cls.company_data["default_journal_sale"]
        cls.revenue = cls.company_data["default_account_revenue"]
        cls.document_type = cls.env["l10n_latam.document.type"].search(
            [("country_id.code", "=", "CL"), ("code", "=", "33")], limit=1
        )
        cls.usd = cls.setup_other_currency("USD", rates=[("1900-01-01", 1.0)])

    def _make_invoice(self, currency=None, with_dte=True):
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": self.journal.id,
                "partner_id": self.partner.id,
                "invoice_date": "2026-10-28",
                "currency_id": (currency or self.company.currency_id).id,
                "l10n_latam_document_type_id": self.document_type.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Servicio de prueba",
                            "quantity": 1,
                            "price_unit": 1000,
                            "account_id": self.revenue.id,
                            "tax_ids": [(6, 0, self.tax_sale_a.ids)],
                        },
                    )
                ],
            }
        )
        move.with_context(l10n_cl_skip_dte_emission=True).action_post()
        if with_dte:
            attachment = self.env["ir.attachment"].create(
                {
                    "name": "DTE_33_63.xml",
                    "raw": TED_XML,
                    "mimetype": "application/xml",
                    "res_model": "account.move",
                    "res_id": move.id,
                    "company_id": self.company.id,
                }
            )
            move._l10n_cl_write_dte_lifecycle_values(
                {"l10n_cl_dte_attachment_id": attachment.id}
            )
        return move

    def _render(self, move):
        content, _mimetype = self.env["ir.actions.report"]._render_qweb_html(
            "account.account_invoices", move.ids
        )
        return content.decode()

    def test_report_shows_the_timbre_resolution_and_references(self):
        move = self._make_invoice()
        self.env["l10n_cl.account.invoice.reference"].create(
            {
                "move_id": move.id,
                "l10n_cl_reference_doc_type_id": self.document_type.id,
                "origin_doc_number": "4567",
                "reason": "Corrección de prueba",
            }
        )
        html = self._render(move)
        self.assertIn('id="l10n_cl_dte_timbre"', html)
        self.assertIn("data:image/png;base64,", html)
        self.assertIn("Timbre electrónico SII", html)
        self.assertIn("9999", html)
        self.assertIn("4567", html)
        self.assertIn("Corrección de prueba", html)

    def test_report_shows_the_total_in_company_currency(self):
        move = self._make_invoice(currency=self.usd)
        html = self._render(move)
        self.assertIn("Total en", html)

    def test_report_without_dte_does_not_render_the_timbre(self):
        move = self._make_invoice(with_dte=False)
        html = self._render(move)
        self.assertNotIn('id="l10n_cl_dte_timbre"', html)
        self.assertNotIn("Timbre electrónico SII", html)
