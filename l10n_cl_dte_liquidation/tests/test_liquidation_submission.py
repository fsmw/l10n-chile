# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import patch

from lxml import etree

from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_gateway.services.production_gateway import (
    SUPPORTED_DTE_CODES,
)
from odoo.addons.l10n_cl_dte_gateway.services.production_response_parser import (
    parse_upload,
)
from odoo.addons.l10n_cl_dte_gateway.services.production_transport import (
    ProductionSiiTransport,
)
from odoo.addons.l10n_cl_dte_gateway.tests.common_certification_request import (
    FakeSiiSession,
    configure_company_certification,
    create_sii_caf,
    dte_status_response,
    fake_sii,
)
from odoo.addons.l10n_cl_dte_gateway.tests.test_certification_transport import (
    upload_response,
)
from odoo.addons.l10n_cl_dte_gateway.tests.test_production_readiness import (
    TestDteProductionReadiness,
)

from ..services.liquidation_xml_composer import LiquidationXmlComposer
from .common import LiquidationTestCase

SII = "{http://www.sii.cl/SiiDte}"


@tagged("post_install", "-at_install")
class TestLiquidationSubmission(LiquidationTestCase):
    """The 43 follows the Demo, Certification and Production submission paths."""

    def test_43_is_in_the_shared_sii_code_sets(self):
        self.assertIn("43", SUPPORTED_DTE_CODES["production"])
        self.assertIn("43", SUPPORTED_DTE_CODES["certification"])
        validator = self.env["l10n_cl.electronic.submission"]._get_document_validator(
            "43"
        )
        self.assertIsInstance(validator, LiquidationXmlComposer)

    def test_demo_submission_validates_the_liquidacion_payload(self):
        self.validate_authorization(document_code="43", folio_start=700, folio_end=702)
        self.create_signing_profile()
        bill = self.standard_settlement()
        document = bill.with_user(self.dte_manager).issue_dte()
        submission = self.submission(document)
        submission._job_send_to_sii()
        self.assertEqual(submission.state, "submitted", submission.sii_message)
        self.assertEqual(document.fiscal_state, "submitted")

    def test_production_upload_sends_the_43_in_an_envio_dte(self):
        TestDteProductionReadiness.configure_company_production(self)
        self.company.sudo().dte_sii_environment = "production"
        TestDteProductionReadiness.create_production_caf(
            self, code="43", folio_start=900
        )
        document = self.standard_settlement().with_user(self.dte_manager).issue_dte()
        submission = self.submission(document)
        receipt = parse_upload(
            upload_response(), self.company.vat, self.company.dte_sii_sender_vat
        )
        with patch.object(
            ProductionSiiTransport, "send_envelope", return_value=receipt
        ) as send:
            submission._job_send_to_sii()
        self.assertTrue(send.called, submission.sii_message)
        envelope = send.call_args.args[2]
        self.assertIn(b"<EnvioDTE", envelope)
        self.assertIn(b"<Liquidacion", envelope)
        self.assertIn(b"<TpoDTE>43</TpoDTE>", envelope)
        self.assertEqual(submission.state, "submitted", submission.sii_message)
        self.assertEqual(document.fiscal_state, "submitted")

    def test_certification_round_trip_is_accepted_by_maullin(self):
        configure_company_certification(self)
        create_sii_caf(self, "certification", code="43", folio_start=300)
        document = self.standard_settlement().with_user(self.dte_manager).issue_dte()
        submission = self.submission(document)
        session = FakeSiiSession(statuses=[dte_status_response("43")])
        with fake_sii(session):
            submission._job_send_to_sii()
            self.assertEqual(submission.state, "submitted", submission.sii_message)
            submission.with_context(
                queue_job__no_delay=True
            ).action_enqueue_status_query()
        self.assertEqual(len(session.uploads), 1)
        envelope = etree.fromstring(session.uploads[0])
        self.assertEqual(
            envelope.findtext(f"{SII}SetDTE/{SII}Caratula/{SII}SubTotDTE/{SII}TpoDTE"),
            "43",
        )
        self.assertIsNotNone(envelope.find(f"{SII}SetDTE/{SII}DTE/{SII}Liquidacion"))
        self.assertTrue(
            all(url.startswith("https://maullin.sii.cl/") for url in session.urls)
        )
        self.assertEqual(submission.state, "accepted", submission.sii_message)
        self.assertEqual(document.fiscal_state, "accepted")
