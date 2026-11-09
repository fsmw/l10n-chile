# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from types import SimpleNamespace
from unittest.mock import MagicMock

from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_certification.models.certification_stage import (
    DteCertificationProcessStage,
)

from .common import DteCertificationTestCase


class _MappedRecords(list):
    def mapped(self, field_name):
        return [getattr(record, field_name) for record in self]


@tagged("post_install", "-at_install")
class TestDteCertificationReconciliation(DteCertificationTestCase):
    def _stage(self, *, submissions=None, envelopes=None):
        stage = SimpleNamespace(
            _check_stage_access=MagicMock(),
            electronic_submission_ids=_MappedRecords(submissions or []),
            sii_envelope_submission_ids=_MappedRecords(envelopes or []),
            _write_internal=MagicMock(),
            _external_result_is_verified=MagicMock(return_value=True),
            process_id=SimpleNamespace(_record_event=MagicMock()),
        )
        return stage

    def test_unknown_outcome_blocks_the_stage_without_clearing_evidence(self):
        stage = self._stage(envelopes=[SimpleNamespace(state="outcome_unknown")])
        DteCertificationProcessStage.action_reconcile([stage])
        values = stage._write_internal.call_args.args[0]
        self.assertEqual(values["state"], "blocked")
        self.assertEqual(values["sii_status"], "outcome_unknown")

    def test_envelope_receipt_alone_does_not_accept_the_dtes(self):
        stage = self._stage(envelopes=[SimpleNamespace(state="accepted")])
        DteCertificationProcessStage.action_reconcile([stage])
        values = stage._write_internal.call_args.args[0]
        self.assertEqual(values["state"], "blocked")
        self.assertEqual(values["sii_status"], "per_document_results_required")

    def test_all_linked_dtes_must_be_accepted_before_stage_acceptance(self):
        accepted_document = SimpleNamespace(fiscal_state="accepted")
        accepted_submission = SimpleNamespace(
            state="accepted", document_id=accepted_document
        )
        stage = self._stage(submissions=[accepted_submission])
        DteCertificationProcessStage.action_reconcile([stage])
        values = stage._write_internal.call_args.args[0]
        self.assertEqual(values["state"], "accepted")
        self.assertEqual(values["sii_status"], "accepted")

    def test_accepted_dtes_still_require_verified_stage_result(self):
        accepted_document = SimpleNamespace(fiscal_state="accepted")
        accepted_submission = SimpleNamespace(
            state="accepted", document_id=accepted_document
        )
        stage = self._stage(submissions=[accepted_submission])
        stage._external_result_is_verified.return_value = False
        DteCertificationProcessStage.action_reconcile([stage])
        values = stage._write_internal.call_args.args[0]
        self.assertEqual(values["state"], "blocked")
        self.assertEqual(values["sii_status"], "external_result_evidence_required")
