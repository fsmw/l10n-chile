# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_gateway.services.production_response_parser import (
    parse_upload,
)
from odoo.addons.l10n_cl_dte_gateway.services.production_transport import (
    ProductionSiiTransport,
)
from odoo.addons.l10n_cl_dte_gateway.tests.test_certification_transport import (
    upload_response,
)
from odoo.addons.l10n_cl_dte_gateway.tests.test_production_readiness import (
    TestDteProductionReadiness,
)

from .common import DteNoteTestCase


@tagged("post_install", "-at_install")
class TestNoteProduction(DteNoteTestCase):
    """Credit notes for Production invoices are issued and sent to SII."""

    def setUp(self):
        super().setUp()
        TestDteProductionReadiness.configure_company_production(self)
        self.company.sudo().dte_sii_environment = "production"

    def _upload(self):
        raw = upload_response().replace(
            b"<RUTCOMPANY>12345678-5", f"<RUTCOMPANY>{self.company.vat}".encode()
        )
        return parse_upload(raw, self.company.vat, self.company.dte_sii_sender_vat)

    def test_production_credit_note_is_signed_and_uploaded(self):
        source, target, _snapshot = self.create_accepted_source(
            "33", environment="production"
        )
        note = self.create_credit_note(source, target, operation="cancel")
        note.action_post()
        self.assertTrue(note.dte_issuance_available)
        TestDteProductionReadiness.create_production_caf(
            self, code="61", folio_start=900
        )
        document = note.with_user(self.dte_manager).issue_dte()
        self.assertEqual(document.sii_environment, "production")
        self.assertEqual(document.note_target_document_id, target)
        submission = (
            self.env["l10n_cl.electronic.submission"]
            .with_user(self.dte_manager)
            .search([("document_id", "=", document.id)])
        )
        self.assertEqual(submission.environment, "production")
        with patch.object(
            ProductionSiiTransport, "send_envelope", return_value=self._upload()
        ) as send:
            # The queue job runs as the user who issued the note.
            submission._job_send_to_sii()
        self.assertEqual(submission.state, "submitted")
        self.assertIn(b"<TipoDTE>61</TipoDTE>", send.call_args.args[2])

    def test_note_cannot_reference_a_dte_from_another_mode(self):
        self.company.sudo().dte_sii_environment = "demo"
        source, target, _snapshot = self.create_accepted_source("33")
        self.company.sudo().dte_sii_environment = "production"
        with self.assertRaises(UserError):
            self.create_credit_note(source, target, operation="cancel")
