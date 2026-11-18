# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Golden-file test for the domestic dispatch guide 52.

The test reviews, freezes and prepares a guide through the real flow (CAF,
folio, TED, XML, signature) and compares the signed XML with a reviewed file
in ``tests/golden/``. See ``l10n_cl_dte_xml/tests/golden.py`` for masking and
regeneration.
"""

import base64
from pathlib import Path

from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase
from odoo.addons.l10n_cl_dte_xml.tests.golden import (
    assert_golden_xml,
    frozen_chile_now,
)

from .common import DispatchRecordMixin

GOLDEN_DIR = Path(__file__).parent / "golden"
ADAPTER_MODULE = "odoo.addons.l10n_cl_dte_dispatch.services.dispatch_issuance_adapter"
TIMESTAMP = "2026-11-12T10:00:00"


@tagged("post_install", "-at_install")
class TestDispatchGoldenXml(DispatchRecordMixin, DteInvoiceTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dispatch_values = cls.build_dispatch_values(cls.receiver)

    def test_guide_52_sale_transfer(self):
        dispatch = self._create_dispatch()
        self._create_line(dispatch, description="Caja de repuestos", quantity=4)
        self._create_line(
            dispatch, description="Pallet de madera", quantity=1, unit_price=5500
        )
        dispatch.action_review()
        dispatch.action_freeze_snapshot()
        self.validate_authorization(document_code="52", folio_start=40, folio_end=45)
        self.create_signing_profile()
        with frozen_chile_now(ADAPTER_MODULE, TIMESTAMP):
            document = dispatch.action_prepare_document()
        signed_xml = base64.b64decode(document._get_signed_artifact().content)
        assert_golden_xml(self, signed_xml, GOLDEN_DIR / "dte_52_sale.xml")
