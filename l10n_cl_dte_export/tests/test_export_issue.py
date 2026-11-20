# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from unittest.mock import patch

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

from ..services.export_xml_composer import ExportXmlComposer
from .common import ExportTestCase


@tagged("post_install", "-at_install")
class TestExportIssue(ExportTestCase):
    """The Issue DTE button turns an export invoice into a 110 sent to the SII."""

    def _issue(self, invoice):
        document = invoice.with_user(self.dte_manager).issue_dte()
        submission = (
            self.env["l10n_cl.electronic.submission"]
            .sudo()
            .search([("document_id", "=", document.id)])
        )
        return document, submission

    def test_demo_issue_signs_a_valid_export(self):
        caf = self.validate_authorization(
            document_code="110", folio_start=80, folio_end=85
        )
        self.create_signing_profile()
        invoice = self._export()
        document, _submission = self._issue(invoice)
        self.assertEqual(document.document_code, "110")
        self.assertEqual(document.folio, caf.folio_start)
        self.assertEqual(
            invoice.l10n_latam_document_number.lstrip("0"), str(document.folio)
        )
        signed = base64.b64decode(document.sudo()._get_signed_artifact().content)
        self.assertIn(b"<Exportaciones", signed)
        self.assertTrue(ExportXmlComposer().validate_document(signed))

    def test_production_upload_wraps_the_export_in_an_envio_dte(self):
        TestDteProductionReadiness.configure_company_production(self)
        self.company.sudo().dte_sii_environment = "production"
        TestDteProductionReadiness.create_production_caf(
            self, code="110", folio_start=900
        )
        document, submission = self._issue(self._export())
        receipt = parse_upload(
            upload_response(),
            self.company.vat,
            self.company.dte_sii_sender_vat,
        )
        with patch.object(
            ProductionSiiTransport, "send_envelope", return_value=receipt
        ) as send:
            submission._job_send_to_sii()
        self.assertTrue(send.called, submission.sii_message)
        envelope = send.call_args.args[2]
        self.assertIn(b"<EnvioDTE", envelope)
        self.assertIn(b"<Exportaciones", envelope)
        self.assertIn(b"<TpoDTE>110</TpoDTE>", envelope)
        self.assertEqual(submission.state, "submitted", submission.sii_message)
        self.assertEqual(document.fiscal_state, "submitted")
