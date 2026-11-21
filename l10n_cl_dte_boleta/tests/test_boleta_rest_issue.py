# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from decimal import Decimal
from unittest.mock import patch

from lxml import etree

from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_gateway.services.boleta_rest import SiiBoletaRestClient
from odoo.addons.l10n_cl_dte_gateway.services.certification_auth import (
    SiiTransportError,
)
from odoo.addons.l10n_cl_dte_gateway.tests.common_certification_request import (
    FakeSiiSession,
    configure_company_certification,
    create_sii_caf,
    fake_sii,
)
from odoo.addons.l10n_cl_dte_gateway.tests.test_production_readiness import (
    TestDteProductionReadiness,
)
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase

from ..services.boleta_rest_gateway import BoletaRestGateway
from ..services.boleta_snapshot import BoletaSnapshotService


@tagged("post_install", "-at_install")
class TestBoletaRestIssue(DteInvoiceTestCase):
    """A Production boleta travels to the SII REST service in an EnvioBOLETA."""

    def setUp(self):
        super().setUp()
        TestDteProductionReadiness.configure_company_production(self)
        self.company.sudo().dte_sii_environment = "production"
        TestDteProductionReadiness.create_production_caf(
            self, code="39", folio_start=700
        )

    def _issue(self):
        invoice = self.make_invoice("39")
        with patch.object(
            BoletaSnapshotService,
            "_annual_135_uf_limit",
            return_value=Decimal("999999999"),
        ):
            document = invoice.with_user(self.dte_manager).issue_dte()
        return document, self.env["l10n_cl.electronic.submission"].sudo().search(
            [("document_id", "=", document.id)]
        )

    def test_boleta_is_uploaded_as_envioboleta_and_accepted(self):
        document, submission = self._issue()
        upload = {
            "state": "submitted",
            "track_id": "4567",
            "transport_code": "REC",
            "raw_response": b'{"trackid": 4567}',
        }
        with patch.object(
            SiiBoletaRestClient, "send_envelope", return_value=upload
        ) as send:
            submission._job_send_to_sii()
        envelope = etree.fromstring(send.call_args.args[1])
        self.assertEqual(etree.QName(envelope).localname, "EnvioBOLETA")
        self.assertEqual(submission.state, "submitted")
        self.assertEqual(submission.track_id, "4567")
        status = {
            "state": "accepted",
            "track_id": "4567",
            "transport_code": "EPR",
            "raw_response": b'{"estado": "EPR"}',
            "error_summary": "",
        }
        with patch.object(SiiBoletaRestClient, "query", return_value=status):
            submission._job_query_sii_status()
        self.assertEqual(submission.state, "accepted")
        self.assertEqual(document.fiscal_state, "accepted")

    def test_failed_authentication_leaves_a_retryable_error(self):
        _document, submission = self._issue()
        offline = SiiTransportError("No seed", dispatched=False)
        with patch.object(SiiBoletaRestClient, "send_envelope", side_effect=offline):
            submission._job_send_to_sii()
        self.assertEqual(submission.state, "error")
        self.assertTrue(submission._was_never_dispatched())


@tagged("post_install", "-at_install")
class TestBoletaCertificationRest(TestBoletaRestIssue):
    """Certification boletas keep the REST channel, never the DTEUpload path.

    The inherited Production scenarios also run here against Certification.
    """

    def setUp(self):
        super(TestBoletaRestIssue, self).setUp()
        configure_company_certification(self)
        create_sii_caf(self, "certification", code="39", folio_start=700)

    def test_certification_boleta_uses_rest_not_envelope_upload(self):
        _document, submission = self._issue()
        self.assertEqual(submission.environment, "certification")
        self.assertFalse(submission._uses_envelope_upload())
        self.assertIsInstance(submission._gateway(), BoletaRestGateway)
        upload = {
            "state": "submitted",
            "track_id": "4568",
            "transport_code": "REC",
            "raw_response": b'{"trackid": 4568}',
        }
        with (
            fake_sii(FakeSiiSession()) as session,
            patch.object(SiiBoletaRestClient, "send_envelope", return_value=upload),
        ):
            submission._job_send_to_sii()
        self.assertEqual(submission.state, "submitted", submission.sii_message)
        self.assertEqual(session.urls, [])
        self.assertFalse(submission.transport_request_artifact_id)
