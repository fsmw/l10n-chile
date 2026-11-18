# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).


from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase

from .common import DispatchRecordMixin

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
RECEIVER = {
    "vat": "76864280-K",
    "name": "Receptor Sintetico",
    "activity": "Comercio",
    "address": "Calle Prueba 200",
    "commune": "Santiago",
    "city": "Santiago",
}


@tagged("post_install", "-at_install")
class TestDispatchSignedGuide(DispatchRecordMixin, DteInvoiceTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dispatch_values = cls.build_dispatch_values(cls.receiver)

    def _signed_guide(self, folio=1):
        dispatch = self._create_dispatch()
        self._create_line(dispatch)
        authorization = self.validate_authorization(
            document_code="52", folio_start=1, folio_end=10
        )
        composer = self.env["l10n_cl.dte.dispatch.xml.composer"]
        self.create_signing_profile()
        xml = composer.compose_signed_guide(
            dispatch,
            folio,
            ISSUER,
            RECEIVER,
            TIMESTAMP,
            authorization,
            signing_environment="demo",
        )
        return xml

    def test_folio_outside_caf_range_is_rejected(self):
        # Arrange / Act / Assert
        with self.assertRaises(ValidationError):
            self._signed_guide(folio=99)
