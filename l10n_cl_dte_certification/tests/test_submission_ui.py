# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
from unittest.mock import patch

from lxml import etree

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tools.safe_eval import safe_eval
from odoo.tools.translate import code_translations

from ..services.set_parser import CertificationSetParser
from .common import DteCertificationTestCase


@tagged("post_install", "-at_install")
class TestCertificationSubmissionUi(DteCertificationTestCase):
    def test_native_python_catalog_translates_rejection_and_query_messages(self):
        gateway = code_translations.get_python_translations(
            "l10n_cl_dte_gateway", "es_CL"
        )
        self.assertIn(
            "codificación",
            gateway.get("The file character encoding was rejected.", ""),
        )
        certification = code_translations.get_python_translations(
            "l10n_cl_dte_certification", "es_CL"
        )
        self.assertEqual(
            certification.get("SII Status Query"), "Consulta de estado al SII"
        )

    def setUp(self):
        super().setUp()
        self._create_run_fixture()

    def _create_run_fixture(self):
        self.env = self.env(user=self.certification_manager)
        source = b"Synthetic transmission UI test"
        certification_set = self.env["l10n_cl.dte.certification.set"]._create_imported(
            {
                "name": "Transmission UI",
                "company_id": self.company.id,
                "source_file": base64.b64encode(source),
                "source_filename": "test.txt",
                "source_digest": hashlib.sha256(source).hexdigest(),
                "source_byte_count": len(source),
                "source_encoding": "utf-8",
                "parser_profile": "sii_txt_v1",
                "parser_version": "test",
                "attention_number": "submission-ui",
            }
        )
        payload = {
            "source_digest": certification_set.source_digest,
            "attention_number": certification_set.attention_number,
            "cases": [],
        }
        certification_set._write_internal(
            {
                "state": "ready",
                "normalized_payload": payload,
                "normalized_digest": CertificationSetParser().canonical_digest(payload),
            }
        )
        self.run = self.env["l10n_cl.dte.certification.run"]._create_for_set(
            certification_set, 1
        )
        self.run._write_internal(
            {
                "state": "exported",
                "envelope_file": base64.b64encode(b"<EnvioDTE/>"),
                "envelope_filename": "test.xml",
                "envelope_digest": hashlib.sha256(b"<EnvioDTE/>").hexdigest(),
                "reportability_state": "ready",
            }
        )

    def _submission(self, state="outcome_unknown", track_id=False):
        submission = self.env["l10n_cl.sii.envelope.submission"]._create_for_run(
            self.run
        )
        submission._write_internal({"state": state, "track_id": track_id})
        return submission

    def _visible_buttons(self):
        view = self.run.get_view(
            view_id=self.env.ref(
                "l10n_cl_dte_certification.certification_run_view_form"
            ).id,
            view_type="form",
        )
        root = etree.fromstring(view["arch"])
        context = {
            name: self.run[name]
            for name in (
                "can_prepare_sii_submission",
                "can_generate",
                "can_regenerate_signatures",
                "can_query_sii_status",
                "can_correct_sii_envelope",
                "envelope_submission_count",
                "state",
                "is_partial",
                "reportability_state",
            )
        }
        return {
            button.get("name")
            for button in root.xpath("./header/button")
            if not safe_eval(button.get("invisible", "False"), context)
        }

    def test_preparation_actions_are_available_before_any_submission(self):
        visible = self._visible_buttons()
        self.assertIn("action_test_sii_authentication", visible)
        self.assertIn("action_export_envelope", visible)
        self.assertIn("action_run_official_preflight", visible)
        self.assertIn("action_submit_official_envelope", visible)
        self.assertNotIn("action_open_sii_submission", visible)

    def test_only_response_button_remains_for_unqueryable_submission(self):
        submission = self._submission()
        for state in (
            "pending",
            "sending",
            "error",
            "outcome_unknown",
            "rejected",
            "accepted",
        ):
            with self.subTest(state=state):
                submission._write_internal({"state": state})
                self.assertEqual(
                    self._visible_buttons(), {"action_open_sii_submission"}
                )

    def test_status_query_button_requires_track_and_eligible_state(self):
        submission = self._submission(track_id="12345")
        for state in ("submitted", "processing", "outcome_unknown"):
            with self.subTest(state=state):
                submission._write_internal({"state": state})
                self.assertEqual(
                    self._visible_buttons(),
                    {
                        "action_open_sii_submission",
                        "action_query_sii_status",
                    },
                )
        for state in (
            "accepted",
            "rejected",
            "accepted_with_observations",
            "partially_accepted",
        ):
            with self.subTest(state=state):
                submission._write_internal({"state": state})
                self.assertEqual(
                    self._visible_buttons(), {"action_open_sii_submission"}
                )

    def test_query_without_track_cannot_schedule_a_job(self):
        submission = self._submission()
        with patch.object(type(submission), "action_enqueue_status_query") as enqueue:
            with self.assertRaises(UserError):
                self.run.action_query_sii_status()
            enqueue.assert_not_called()

    def test_query_with_track_queues_only_the_status_operation(self):
        submission = self._submission(state="submitted", track_id="12345")
        with (
            patch.object(type(submission), "action_enqueue_status_query") as query,
            patch.object(type(submission), "action_enqueue_send") as send,
        ):
            self.run.action_query_sii_status()
            query.assert_called_once()
            send.assert_not_called()

    def _record_rejection(self, submission):
        response = (
            b"<RECEPCIONDTE><RUTSENDER>11111111-1</RUTSENDER>"
            b"<RUTCOMPANY>22222222-2</RUTCOMPANY><STATUS>7</STATUS>"
            b"<DETAIL><ERROR>CHR-00001: Invalid Character Set</ERROR></DETAIL>"
            b"</RECEPCIONDTE>"
        )
        attempt = self.env["l10n_cl.sii.envelope.attempt"]._create_for_operation(
            submission, "send"
        )
        attempt._complete(
            "unknown",
            response,
            error_summary=(
                "The SII upload receipt belongs to a different issuer or sender."
            ),
        )
        return attempt

    def test_receipt_rejection_is_visible_without_promoting_unverified_state(self):
        submission = self._submission()
        self._record_rejection(submission)
        self.assertTrue(self.run.sii_reported_rejection)
        self.assertEqual(self.run.sii_response_code, "7")
        self.assertIn("CHR-00001", self.run.sii_response_detail)
        self.assertIn("encoding", self.run.sii_response_notice)
        self.assertTrue(self.run.sii_response_warning)
        self.assertEqual(submission.state, "outcome_unknown")
        self.assertEqual(self.run.reportability_state, "outcome_unknown")
        self.assertFalse(submission.track_id)
        self.assertEqual(self._visible_buttons(), {"action_open_sii_submission"})

    def test_integrity_failure_does_not_present_a_trusted_receipt(self):
        submission = self._submission()
        attempt = self._record_rejection(submission)
        attachment = (
            self.env["ir.attachment"]
            .sudo()
            .search(
                [
                    ("res_model", "=", attempt._name),
                    ("res_id", "=", attempt.id),
                    ("res_field", "=", "response_xml"),
                ]
            )
        )
        attachment.write({"datas": base64.b64encode(b"tampered")})
        attempt.invalidate_recordset(["response_xml"])
        submission.invalidate_recordset()
        self.assertFalse(submission.reported_rejection)
        self.assertFalse(submission.response_code)
        self.assertTrue(submission.response_warning)

    def test_response_evidence_is_not_exposed_to_dte_operators(self):
        submission = self._submission()
        self._record_rejection(submission)
        with self.assertRaises(AccessError):
            self.run.with_user(self.dte_user).read(["sii_response_detail"])

    def test_archived_set_keeps_response_access_without_operational_buttons(self):
        self._submission(state="submitted", track_id="12345")
        self.run.set_id._write_internal({"state": "archived"})
        self.assertEqual(self._visible_buttons(), {"action_open_sii_submission"})
        with self.assertRaises(UserError):
            self.run.action_query_sii_status()

    def test_response_opens_within_the_set(self):
        submission = self._submission()
        action = self.run.action_open_sii_submission()
        self.assertEqual(action["target"], "new")
        self.assertEqual(action["res_id"], submission.id)
