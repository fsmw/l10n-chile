# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_gateway.tests.common_certification_request import (
    FakeSiiSession,
    assert_certification_round_trip,
    configure_company_certification,
    create_sii_caf,
    fake_sii,
)
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase

from .common import DispatchRecordMixin


@tagged("post_install", "-at_install")
class TestDispatchCertification(DispatchRecordMixin, DteInvoiceTestCase):
    """A dispatch-sourced guide 52 is issued to SII Certification."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dispatch_values = cls.build_dispatch_values(cls.receiver)

    def setUp(self):
        super().setUp()
        configure_company_certification(self)
        self.caf = create_sii_caf(self, "certification", code="52", folio_start=500)

    def _issue(self):
        dispatch = self._create_dispatch()
        self._create_line(dispatch)
        dispatch.action_review()
        dispatch.action_freeze_snapshot()
        document = dispatch.action_issue_document()
        self.assertEqual(document.source_dispatch_id, dispatch)
        submission = (
            self.env["l10n_cl.electronic.submission"]
            .sudo()
            .search([("document_id", "=", document.id)])
        )
        return document, submission

    def test_guide_52_is_sent_to_maullin_and_accepted(self):
        document, submission = self._issue()
        self.assertEqual(document.folio, 500)
        assert_certification_round_trip(self, submission, "52")

    def test_production_caf_blocks_certification_guide_dispatch(self):
        _document, submission = self._issue()
        self.env.cr.execute(
            "UPDATE l10n_cl_caf_authorization SET sii_environment = 'production' "
            "WHERE id = %s",
            [self.caf.id],
        )
        self.caf.invalidate_recordset()
        with fake_sii(FakeSiiSession()) as session:
            submission._job_send_to_sii()
        self.assertEqual(submission.state, "error")
        self.assertIn("Certification CAF", submission.sii_message)
        self.assertEqual(session.urls, [])
