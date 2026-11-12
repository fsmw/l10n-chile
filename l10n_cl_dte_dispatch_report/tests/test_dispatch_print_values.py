# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_dispatch.tests.common import DispatchRecordMixin
from odoo.addons.l10n_cl_dte_dispatch_report.report.dispatch_print_values import (
    extract_guide_print_values,
)
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase

TIMESTAMP = "2026-11-12T10:00:00"
ISSUER = {
    "vat": "76864280-K",
    "name": "Emisor Sintetico de Pruebas",
    "activity": "Servicios informaticos",
    "activity_code": "620200",
    "address": "Avenida Prueba 100",
    "commune": "Santiago",
    "city": "Santiago",
}


@tagged("post_install", "-at_install")
class TestDispatchPrintValues(DispatchRecordMixin, DteInvoiceTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dispatch_values = cls.build_dispatch_values(cls.receiver)

    def _signed_guide(self, transport=None):
        dispatch = self._create_dispatch()
        self._create_line(dispatch, quantity=2.0, unit_price=1000.0)
        self._create_line(
            dispatch, quantity=1.0, unit_price=500.0, tax_category="exempt"
        )
        self._create_line(
            dispatch, quantity=3.0, unit_price=0.0, tax_category="unvalued"
        )
        authorization = self.validate_authorization(
            document_code="52", folio_start=1, folio_end=10
        )
        self.create_signing_profile()
        composer = self.env["l10n_cl.dte.dispatch.xml.composer"]
        return composer.compose_signed_guide(
            dispatch,
            1,
            ISSUER,
            {
                "vat": self.receiver.vat,
                "name": self.receiver.name,
                "address": "Calle Prueba 200",
                "commune": "Santiago",
                "city": "Santiago",
            },
            TIMESTAMP,
            authorization,
            signing_environment="demo",
            transport=transport,
        )

    def test_reads_identity_totals_and_lines(self):
        # Arrange
        xml = self._signed_guide()
        # Act
        values = extract_guide_print_values(xml)
        # Assert
        self.assertEqual(values["document_id"], "F0000000001T52")
        self.assertEqual(values["folio"], "1")
        self.assertEqual(values["issue_date"], "2026-11-12")
        self.assertEqual(values["transfer_indicator"], "1")
        self.assertEqual(values["issuer"]["vat"], "76864280-K")
        self.assertEqual(values["totals"]["net"], "2000")
        self.assertEqual(values["totals"]["exempt"], "500")
        self.assertEqual(values["totals"]["vat"], "380")
        self.assertEqual(values["totals"]["total"], "2880")
        self.assertEqual(len(values["lines"]), 3)
        self.assertEqual(values["lines"][2]["exempt_indicator"], "2")

    def test_transport_is_empty_without_transport_data(self):
        # Arrange / Act
        values = extract_guide_print_values(self._signed_guide())
        # Assert
        self.assertEqual(values["transport"], {})

    def test_transport_is_read_when_given(self):
        # Arrange
        xml = self._signed_guide(transport={"vehicle_plate": "ABCD12"})
        # Act
        values = extract_guide_print_values(xml)
        # Assert
        self.assertEqual(values["transport"]["vehicle_plate"], "ABCD12")

    def test_ted_is_the_literal_signed_segment(self):
        # Arrange
        xml = self._signed_guide()
        # Act
        values = extract_guide_print_values(xml)
        # Assert
        self.assertTrue(values["ted"].startswith(b"<TED"))
        self.assertTrue(values["ted"].endswith(b"</TED>"))
        self.assertIn(values["ted"], xml)
