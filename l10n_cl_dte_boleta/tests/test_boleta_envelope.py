# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from decimal import Decimal
from unittest.mock import patch

from lxml import etree

from odoo import fields
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase

from ..services.boleta_envelope_composer import BoletaEnvelopeComposer
from ..services.boleta_snapshot import BoletaSnapshotService
from ..services.boleta_xml_composer import BoletaXmlComposer

NS = {"s": "http://www.sii.cl/SiiDte"}


class TestBoletaEnvelope(DteInvoiceTestCase):
    """A signed boleta travels in a signed, schema-valid EnvioBOLETA."""

    def _signed_boleta(self, code="39"):
        invoice = self.make_invoice(code, taxable=code == "39")
        caf = self.validate_authorization(
            document_code=code, folio_start=600, folio_end=603
        )
        service = BoletaSnapshotService()
        with patch.object(
            service, "_annual_135_uf_limit", return_value=Decimal("999999999")
        ):
            snapshot = service.capture_invoice(invoice, caf, 600, code)
        composer = BoletaXmlComposer()
        snapshot["ted_xml"] = composer.compose_ted(snapshot, caf)
        unsigned = composer.compose_unsigned_document(snapshot)
        self.company.sudo().write(
            {
                "dte_sii_environment": "certification",
                "dte_sii_sender_vat": "12345678-5",
                "dte_sii_resolution_number": "0",
                "dte_sii_resolution_date": fields.Date.today(),
            }
        )
        self.create_signing_profile(environment="certification")
        company = self.company.with_user(self.dte_manager)
        return company, SignatureService().sign_document(
            company, "certification", unsigned
        )

    def test_boleta_is_wrapped_in_a_signed_envioboleta(self):
        company, signed = self._signed_boleta()
        envelope = BoletaEnvelopeComposer().compose(company, signed, "certification")
        root = etree.fromstring(envelope)
        self.assertEqual(etree.QName(root).localname, "EnvioBOLETA")
        self.assertEqual(
            root.findtext(".//s:Caratula/s:RutReceptor", namespaces=NS), "60803000-K"
        )
        self.assertEqual(root.findtext(".//s:SubTotDTE/s:TpoDTE", namespaces=NS), "39")
        self.assertEqual(len(root.findall("s:SetDTE/s:DTE", NS)), 1)

    def test_only_boletas_travel_in_an_envioboleta(self):
        company, signed = self._signed_boleta()
        forged = signed.replace(b"<TipoDTE>39</TipoDTE>", b"<TipoDTE>33</TipoDTE>")
        with self.assertRaises(ValidationError):
            BoletaEnvelopeComposer().compose(company, forged, "certification")
