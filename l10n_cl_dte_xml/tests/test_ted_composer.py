from copy import deepcopy

from lxml import etree

from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import (
    DteXmlComposer,
    _extract_dd_bytes,
)

from .common import DteInvoiceTestCase


class TestTedComposer(DteInvoiceTestCase):
    def test_ted_uses_reserved_folio_and_caf(self):
        _invoice, snapshot, authorization = self.valid_invoice_snapshot("33", folio=101)

        ted = DteXmlComposer().compose_ted(snapshot, authorization)
        root = etree.fromstring(ted)

        self.assertEqual(root.findtext("DD/F"), "101")
        self.assertEqual(root.findtext("DD/TD"), "33")
        self.assertEqual(root.find("DD/CAF/DA/RE").text, "76864280-K")

    def test_ted_tampering_fails_verification(self):
        _invoice, snapshot, authorization = self.valid_invoice_snapshot("33")
        ted = DteXmlComposer().compose_ted(snapshot, authorization)
        root = etree.fromstring(ted)
        dd = root.find("DD")
        caf = dd.find("CAF")
        frmt = root.findtext("FRMT")
        serialized_dd = _extract_dd_bytes(ted)
        signer = SignatureService()

        self.assertTrue(signer.verify_ted(serialized_dd, frmt, caf))
        dd.find("F").text = "999"
        tampered_dd = serialized_dd.replace(b"<F>100</F>", b"<F>999</F>")
        with self.assertRaises(ValidationError):
            signer.verify_ted(tampered_dd, frmt, caf)

    def test_ted_identity_mismatch_rejected(self):
        _invoice, snapshot, authorization = self.valid_invoice_snapshot("33")
        composer = DteXmlComposer()
        wrong_company = deepcopy(snapshot)
        wrong_company["company_id"] += 1
        wrong_type = deepcopy(snapshot)
        wrong_type["document_code"] = "34"
        wrong_folio = deepcopy(snapshot)
        wrong_folio["folio"] = authorization.folio_end + 1

        for invalid_snapshot in (wrong_company, wrong_type, wrong_folio):
            with self.subTest(snapshot=invalid_snapshot):
                with self.assertRaises(ValidationError):
                    composer.compose_ted(invalid_snapshot, authorization)
