# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_gateway.tests.common_certification_request import (
    assert_certification_round_trip,
    configure_company_certification,
    create_sii_caf,
)

from .common import DteNoteTestCase


@tagged("post_install", "-at_install")
class TestNoteCertification(DteNoteTestCase):
    """Credit and debit notes are issued to SII Certification like Production."""

    def setUp(self):
        super().setUp()
        configure_company_certification(self)

    def _submission(self, document):
        return (
            self.env["l10n_cl.electronic.submission"]
            .sudo()
            .search([("document_id", "=", document.id)])
        )

    def _issue_credit_note(self):
        source, target, _snapshot = self.create_accepted_source(
            "33", environment="certification"
        )
        note = self.create_credit_note(source, target, operation="cancel")
        note.action_post()
        self.assertTrue(note.dte_issuance_available)
        create_sii_caf(self, "certification", code="61", folio_start=900)
        document = note.with_user(self.dte_manager).issue_dte()
        self.assertEqual(document.note_target_document_id, target)
        return note, document

    def test_credit_note_61_is_sent_to_maullin_and_accepted(self):
        _note, document = self._issue_credit_note()
        assert_certification_round_trip(self, self._submission(document), "61")

    def test_debit_note_56_cancels_an_accepted_certification_credit_note(self):
        credit, credit_document = self._issue_credit_note()
        assert_certification_round_trip(self, self._submission(credit_document), "61")
        wizard = (
            self.env["account.debit.note"]
            .with_user(self.dte_manager)
            .with_context(active_model="account.move", active_ids=credit.ids)
            .create(
                {
                    "move_ids": [(6, 0, credit.ids)],
                    "reason": "Anula nota de credito",
                    "dte_note_operation": "cancel",
                }
            )
        )
        debit = self.env["account.move"].browse(wizard.create_debit()["res_id"])
        self.assertEqual(debit.dte_note_target_document_id, credit_document)
        debit.action_post()
        create_sii_caf(self, "certification", code="56", folio_start=950)
        document = debit.with_user(self.dte_manager).issue_dte()
        self.assertEqual(document.document_code, "56")
        assert_certification_round_trip(self, self._submission(document), "56")
