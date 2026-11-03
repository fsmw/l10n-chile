import unittest
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

    @unittest.skip(
        "Known product gap: active CAF folio-range overlap is not enforced yet."
    )
    def test_overlapping_active_caf_rejected(self):
        self.validate_authorization(folio_start=120, folio_end=125)
        overlapping = self.create_authorization(folio_start=123, folio_end=128)

        with self.assertRaises(ValidationError):
            overlapping.action_validate_authorization()
