import base64
import xml.etree.ElementTree as ElementTree
from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError

from .common import CafTestCase


class TestCafAuthorization(CafTestCase):
    def test_authorization_matches_caf(self):
        authorization = self.validate_authorization()

        self.assertEqual(authorization.state, "validated")
        self.assertEqual(authorization.folio_start, 100)
        self.assertEqual(authorization.folio_end, 103)
        self.assertTrue(authorization.validated_at)
        self.assertEqual(authorization.validated_by, self.dte_manager)
        self.assertEqual(authorization.caf_key_id, self.sii_key_id)
        self.assertEqual(authorization.sii_signature_status, "verified")

    def test_missing_sii_trust_key_does_not_block_caf_issuance_setup(self):
        authorization = self.create_authorization(key_id="unpublished-idk")

        authorization.action_validate_authorization()

        self.assertEqual(authorization.state, "validated")
        self.assertEqual(authorization.caf_key_id, "unpublished-idk")
        self.assertEqual(authorization.sii_signature_status, "key_unavailable")

    def test_invalid_sii_signature_is_rejected_when_trust_key_exists(self):
        caf = ElementTree.fromstring(self.build_caf_xml())
        caf.find("CAF/FRMA").text = base64.b64encode(b"invalid signature").decode()
        authorization = self.create_authorization(
            caf_bytes=ElementTree.tostring(caf, encoding="ISO-8859-1")
        )

        with self.assertRaises(ValidationError):
            authorization.action_validate_authorization()

    def test_authorization_scope_and_dates_checked(self):
        wrong_type = self.create_authorization(
            document_code="34", file_document_code="33"
        )
        expired = self.create_authorization(
            folio_start=110,
            folio_end=113,
            date_to=fields.Date.to_string(fields.Date.today() - timedelta(days=1)),
        )

        with self.assertRaises(ValidationError):
            wrong_type.action_validate_authorization()
        with self.assertRaises(ValidationError):
            expired.action_validate_authorization()

    def test_overlapping_active_caf_rejected(self):
        self.validate_authorization(folio_start=120, folio_end=125)
        overlapping = self.create_authorization(folio_start=123, folio_end=128)

        with self.assertRaises(ValidationError):
            overlapping.action_validate_authorization()
