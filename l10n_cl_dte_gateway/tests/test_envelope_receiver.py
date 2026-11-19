# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from ..services.envelope_composer import SiiEnvelopeComposer
from ..services.production_response_parser import parse_xml
from .common_production_request import ProductionRequestCase

CARATULA = "{http://www.sii.cl/SiiDte}SetDTE/{http://www.sii.cl/SiiDte}Caratula"


@tagged("post_install", "-at_install")
class TestEnvelopeReceiver(ProductionRequestCase):
    """The receiver copy of an EnvioDTE names the customer, not SII."""

    def _compose(self, **kwargs):
        company = self.company.sudo()
        envelope = SiiEnvelopeComposer().compose(
            company,
            [self.signed],
            {
                "id": "SetDTE-receiver",
                "sender_vat": company.dte_sii_sender_vat,
                "resolution_number": company.dte_sii_resolution_number,
                "resolution_date": company.dte_sii_resolution_date,
            },
            environment="production",
            **kwargs,
        )
        return parse_xml(envelope).find(CARATULA)

    def test_envelope_for_sii_keeps_the_sii_receiver(self):
        self.assertEqual(
            self._compose().findtext("{http://www.sii.cl/SiiDte}RutReceptor"),
            "60803000-K",
        )

    def test_envelope_for_the_customer_uses_its_rut(self):
        cover = self._compose(receiver_vat="76.086.428-5")
        self.assertEqual(
            cover.findtext("{http://www.sii.cl/SiiDte}RutReceptor"), "76086428-5"
        )

    def test_receiver_without_rut_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._compose(receiver_vat=False)
