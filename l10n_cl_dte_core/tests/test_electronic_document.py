from unittest.mock import patch

import psycopg2

from odoo.exceptions import AccessError, ValidationError

from .common import DteTestCase


class TestElectronicDocument(DteTestCase):
    def test_state_transition_records_event(self):
        document = self.create_document()
        caf = self.create_artifact(document, "caf_source")

        document.action_record_transition(
            "authorized", "test_caf_validated", artifact_id=caf.id
        )

        self.assertEqual(document.preparation_state, "authorized")
        event = document.event_ids.filtered(
            lambda item: item.event_code == "test_caf_validated"
        )
        self.assertEqual(len(event), 1)
        self.assertEqual(event.actor_id, self.dte_manager)

    def test_forbidden_transition_preserves_state(self):
        document = self.create_document()

        with self.assertRaises(ValidationError):
            document.action_record_transition("signed", "invalid_transition")

        self.assertEqual(document.preparation_state, "draft")
        self.assertFalse(document.folio)

    def test_duplicate_identity_rejected(self):
        document = self.create_document(source_key="stable-source-key")

        with patch("odoo.sql_db._logger.error"):
            with self.assertRaises(psycopg2.IntegrityError), self.env.cr.savepoint():
                self.create_document(source_key=document.source_key)

    def test_company_read_isolation(self):
        other_company = (
            self.env["res.company"].sudo().create({"name": "Other DTE test company"})
        )
        other_document = self.env["l10n_cl.electronic.document"].create(
            {
                "name": "Other company document",
                "company_id": other_company.id,
                "sii_environment": other_company.dte_sii_environment,
                "document_code": "33",
                "source_key": "other-company-source",
            }
        )

        visible = (
            self.env["l10n_cl.electronic.document"]
            .with_user(self.dte_user)
            .search([("id", "=", other_document.id)])
        )

        self.assertFalse(visible)
        with self.assertRaises(AccessError):
            other_document.with_user(self.dte_user).read(["name"])
