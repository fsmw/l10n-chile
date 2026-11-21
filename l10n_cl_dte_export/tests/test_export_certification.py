# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_gateway.tests.common_certification_request import (
    assert_certification_round_trip,
    configure_company_certification,
    create_sii_caf,
)

from .common import ExportNoteCase


@tagged("post_install", "-at_install")
class TestExportCertification(ExportNoteCase):
    """Exports 110 and their notes 111/112 are issued to SII Certification."""

    def setUp(self):
        # Skip the Demo CAF and signing profile of ExportNoteCase.
        super(ExportNoteCase, self).setUp()
        configure_company_certification(self)
        for folio, code in enumerate(("110", "111", "112"), start=1):
            create_sii_caf(self, "certification", code=code, folio_start=folio * 100)

    def _send(self, move, code):
        document = self._issue(move)
        self.assertEqual(document.document_code, code)
        submission = (
            self.env["l10n_cl.electronic.submission"]
            .sudo()
            .search([("document_id", "=", document.id)])
        )
        upload = assert_certification_round_trip(self, submission, code)
        # The export validator, not the national one, checked the envelope.
        self.assertIn(b"<Exportaciones", upload)
        return document

    def test_export_110_and_notes_111_112_are_accepted_in_certification(self):
        self.root = self._send(self._export(), "110")
        debit = self._send(self._note("111", 500), "111")
        credit = self._send(self._note("112", 1000), "112")
        self.assertEqual(debit.export_root_document_id, self.root)
        self.assertEqual(credit.export_root_document_id, self.root)
