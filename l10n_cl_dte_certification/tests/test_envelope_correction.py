# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from lxml import etree

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tools.safe_eval import safe_eval

from odoo.addons.l10n_cl_dte_caf.tests.common import CafTestCase
from odoo.addons.l10n_cl_dte_gateway.services.certification_auth import (
    CertificationAuthentication,
)
from odoo.addons.l10n_cl_dte_gateway.services.envelope_composer import (
    SiiEnvelopeComposer,
)
from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_signing.tests.common import SigningTestCase
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import DteXmlComposer

from ..services.case_generation_service import CertificationCaseGenerationService
from ..services.official_set_preflight import OfficialCertificationSetPreflight
from ..services.rejected_document_correction import (
    RejectedCertificationDocumentCorrection,
)
from ..services.set_execution_service import CertificationSetExecutionService
from ..services.set_parser import CertificationSetParser
from .common import DteCertificationTestCase
from .test_submission_ui import TestCertificationSubmissionUi


@tagged("post_install", "-at_install")
class TestEnvelopeCorrection(DteCertificationTestCase, SigningTestCase):
    def setUp(self):
        super().setUp()
        self.env = self.env(user=self.certification_manager)
        self.company = self.company.with_env(self.env)
        self.profile = self.create_signing_profile(environment="certification")
        # Reuse the validated small SET fixture without inheriting its test cases.
        TestCertificationSubmissionUi._create_run_fixture(self)
        self.company = self.company.with_env(self.env)
        self.company.partner_id.vat = "76086428-5"
        self.company.dte_sii_sender_vat = "76086428-5"
        self.company.write(
            {
                "dte_sii_resolution_number": "0",
                "dte_sii_resolution_date": "2020-01-01",
                "dte_certification_resolution_date": "2020-01-01",
            }
        )
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(
            patch.object(DteXmlComposer, "validate_document", return_value=True)
        )
        self.stack.enter_context(
            patch(
                "odoo.addons.l10n_cl_dte_gateway.services.envelope_composer._schema",
                return_value=MagicMock(spec=etree.XMLSchema),
            )
        )
        self.stack.enter_context(
            patch.object(type(self.run), "_run_official_preflight", return_value={})
        )
        self.signed = SignatureService().sign_document(
            self.company,
            "certification",
            (
                b'<DTE xmlns="http://www.sii.cl/SiiDte">'
                b'<Documento ID="F1">'
                b"<Encabezado>"
                b"<IdDoc>"
                b"<TipoDTE>33</TipoDTE>"
                b"</IdDoc>"
                b"<Emisor>"
                b"<RUTEmisor>76086428-5</RUTEmisor>"
                b"</Emisor>"
                b"</Encabezado>"
                b"</Documento>"
                b"</DTE>"
            ),
        )
        self.stack.enter_context(
            patch.object(
                CertificationSetExecutionService,
                "validate_case_result",
                return_value=self.signed,
            )
        )
        # Source case identity/coverage remain real; minimal DTE is crypto-tested.
        self.run.set_id._write_internal({"state": "mapping"})
        case = self.env["l10n_cl.dte.certification.case"]._create_imported(
            {
                "set_id": self.run.set_id.id,
                "sequence": 1,
                "case_identifier": "submission-ui-1",
                "document_code": "33",
                "source_start_line": 1,
                "source_end_line": 1,
                "instruction_text": "Synthetic envelope correction",
            }
        )
        self.env["l10n_cl.dte.certification.run.line"]._create_for_run(
            self.run, case
        )._write_internal({"selected": True, "state": "prepared"})
        self.run.set_id._write_internal({"state": "ready"})
        envelope = SiiEnvelopeComposer().compose(
            self.company,
            [self.signed],
            {
                "id": "SET-submission-ui-1",
                "sender_vat": "76086428-5",
                "resolution_number": "0",
                "resolution_date": "2020-01-01",
            },
        )
        self.run._write_internal(
            {
                "envelope_file": base64.b64encode(envelope),
                "envelope_digest": hashlib.sha256(envelope).hexdigest(),
            }
        )
        self.submission = self.env["l10n_cl.sii.envelope.submission"]._create_for_run(
            self.run
        )
        self.submission._write_internal({"state": "outcome_unknown"})
        self.attempt = self.env["l10n_cl.sii.envelope.attempt"]._create_for_operation(
            self.submission, "send"
        )
        self.receipt = (
            b"<RECEPCIONDTE>"
            b"<RUTSENDER>012345678-5</RUTSENDER>"
            b"<RUTCOMPANY>76086428-5</RUTCOMPANY>"
            b"<STATUS>7</STATUS>"
            b"<DETAIL>"
            b"<ERROR>CHR-00001: Invalid Character Set</ERROR>"
            b"</DETAIL>"
            b"</RECEPCIONDTE>"
        )
        self.attempt._complete("unknown", self.receipt, "Identity mismatch")

    def test_correction_preserves_sent_evidence_documents_and_folios(self):
        previous = self.run.envelope_file
        input_digest = self.run.input_digest
        self.assertTrue(self.run.can_correct_sii_envelope)
        with patch.object(CertificationAuthentication, "post") as network:
            self.run.action_correct_sii_envelope()
            network.assert_not_called()
        self.assertNotEqual(self.run.envelope_file, previous)
        self.assertEqual(self.submission.request_xml, previous)
        self.assertEqual(self.attempt.response_xml, base64.b64encode(self.receipt))
        self.assertEqual(self.submission.state, "outcome_unknown")
        self.assertEqual(self.run.input_digest, input_digest)
        self.assertEqual(self.company.dte_sii_sender_vat, "12345678-5")
        new = base64.b64decode(self.run.envelope_file)
        self.assertEqual(
            new.splitlines()[0], b'<?xml version="1.0" encoding="ISO-8859-1"?>'
        )
        SiiEnvelopeComposer().validate_upload_format(new)
        SignatureService().verify_envelope_signature(new)
        self.assertFalse(
            OfficialCertificationSetPreflight._envelope_matches_run(self.run)
        )
        embedded = etree.fromstring(new).xpath(".//*[local-name()='DTE']")[0]
        SignatureService().verify_signature(etree.tostring(embedded))
        self.assertEqual(len(self.run.signature_regeneration_ids), 1)
        self.assertEqual(
            self.run.signature_regeneration_ids.previous_envelope_file, previous
        )
        self.assertTrue(self.run.can_prepare_sii_submission)
        self.assertFalse(self.run.can_correct_sii_envelope)

    def _record_cover_rejection(self, status="RCT", track="12345"):
        self.attempt._complete(
            "completed",
            b"<RECEPCIONDTE><RUTSENDER>12345678-5</RUTSENDER>"
            b"<RUTCOMPANY>76086428-5</RUTCOMPANY><STATUS>0</STATUS>"
            b"<TRACKID>12345</TRACKID></RECEPCIONDTE>",
        )
        self.submission._write_internal({"state": "rejected", "track_id": "12345"})
        attempt = self.env["l10n_cl.sii.envelope.attempt"]._create_for_operation(
            self.submission, "status"
        )
        root = etree.Element("{http://schemas.xmlsoap.org/soap/envelope/}Envelope")
        body = etree.SubElement(root, "{http://schemas.xmlsoap.org/soap/envelope/}Body")
        etree.SubElement(body, "getEstUpReturn").text = (
            f"<RESPUESTA><RESP_HDR><TRACKID>{track}</TRACKID>"
            f"<ESTADO>{status}</ESTADO><GLOSA>Caratula</GLOSA>"
            "</RESP_HDR></RESPUESTA>"
        )
        attempt._complete("completed", etree.tostring(root))
        return attempt

    def test_verified_rct_correction_preserves_track_and_production_resolution(self):
        self.company.dte_sii_resolution_number = "80"
        self.company.dte_sii_resolution_date = "2014-08-22"
        self.company.dte_certification_resolution_date = "2026-08-14"
        self._record_cover_rejection()
        old = self.submission.request_xml
        self.assertEqual(self.submission._verified_schema_rejection()["code"], "RCT")
        self.assertTrue(self.run.can_correct_sii_envelope)
        self.run.action_correct_sii_envelope()
        self.assertEqual(self.company.dte_sii_resolution_number, "80")
        self.assertEqual(self.run.set_id.resolution_number, "0")
        self.assertEqual(str(self.company.dte_sii_resolution_date), "2014-08-22")
        self.assertEqual(self.submission.track_id, "12345")
        self.assertEqual(self.submission.state, "rejected")
        self.assertEqual(self.submission.request_xml, old)
        new = etree.fromstring(base64.b64decode(self.run.envelope_file))
        self.assertEqual(new.xpath("string(.//*[local-name()='NroResol'])"), "0")
        self.assertEqual(
            new.xpath("string(.//*[local-name()='FchResol'])"), "2026-08-14"
        )
        original = etree.fromstring(base64.b64decode(old))
        self.assertEqual(
            etree.tostring(original.xpath(".//*[local-name()='DTE']")[0]),
            etree.tostring(new.xpath(".//*[local-name()='DTE']")[0]),
        )
        audit = self.run.signature_regeneration_ids.signature_changes[0]
        self.assertEqual(audit["previous_resolution_date"], "2020-01-01")
        self.assertEqual(audit["replacement_resolution_date"], "2026-08-14")
        self.assertEqual(audit["company_resolution_date"], "2014-08-22")
        self.assertTrue(self.run.can_prepare_sii_submission)
        proof = self.run.signature_regeneration_ids.signature_changes[0]["receipts"][0]
        self.assertEqual(proof["code"], "RCT")
        self.assertEqual(proof["track_id"], "12345")

    def test_correction_requires_a_separate_certification_resolution_date(self):
        self._record_cover_rejection()
        self.company.dte_certification_resolution_date = False
        previous = self.run.envelope_file
        with self.assertRaisesRegex(UserError, "SII Certification registry"):
            self.run.action_correct_sii_envelope()
        self.assertEqual(self.run.envelope_file, previous)
        self.assertFalse(self.run.signature_regeneration_ids)

    def _attach_signed_document(self, signed):
        # This minimal crypto fixture has no accounting move. Real execution
        # link validation is exercised by the SET execution regression suite.
        self.stack.enter_context(
            patch.object(type(self.run), "_check_execution_access", return_value=True)
        )
        document = self.create_document(sii_environment="certification")
        caf = self.create_artifact(document, "caf_source")
        document.action_record_transition("authorized", "test_caf", artifact_id=caf.id)
        document.action_record_transition(
            "numbered", "test_folio", artifact_id=caf.id, folio_number=1
        )
        snapshot = self.create_artifact(document, "source_snapshot", b"{}")
        document.action_record_source_snapshot(snapshot.id)
        prepared = self.create_artifact(document, "unsigned_xml", b"<DTE/>")
        document.action_record_transition(
            "prepared", "test_prepared", artifact_id=prepared.id
        )
        original = self.create_artifact(document, "signed_xml", signed)
        document.action_record_transition(
            "signed", "test_signed", artifact_id=original.id
        )
        self.run.line_ids._write_internal({"document_id": document.id})
        return original.with_env(self.env)

    def _record_document_rejection(self, informed=1, rejected=1, observations=0):
        self._record_cover_rejection()
        root = etree.Element("{http://schemas.xmlsoap.org/soap/envelope/}Envelope")
        body = etree.SubElement(root, "{http://schemas.xmlsoap.org/soap/envelope/}Body")
        etree.SubElement(body, "getEstUpReturn").text = (
            "<RESPUESTA><RESP_HDR><TRACKID>12345</TRACKID>"
            "<ESTADO>EPR</ESTADO><GLOSA>Envio Procesado</GLOSA></RESP_HDR>"
            "<RESP_BODY><TIPO_DOCTO>33</TIPO_DOCTO>"
            f"<INFORMADOS>{informed}</INFORMADOS>"
            f"<ACEPTADOS>{informed - rejected - observations}</ACEPTADOS>"
            f"<RECHAZADOS>{rejected}</RECHAZADOS><REPAROS>{observations}</REPAROS>"
            "</RESP_BODY></RESPUESTA>"
        )
        self.submission.attempt_ids[:1]._complete("completed", etree.tostring(root))

    def _prepare_new_execution_test(self, *, observations=0, rejected=0):
        self.run.set_id.case_ids._write_internal({"readiness": "ready"})
        self._record_document_rejection(rejected=rejected, observations=observations)
        state = (
            "rejected"
            if rejected
            else "accepted_with_observations"
            if observations
            else "accepted"
        )
        self.submission._write_internal({"state": state})
        return self.run.set_id

    def _assert_new_execution_preserves_original(self, *, observations=0, rejected=0):
        certification_set = self._prepare_new_execution_test(
            observations=observations, rejected=rejected
        )
        original = (
            self.run.envelope_file,
            self.run.envelope_digest,
            self.run.input_digest,
        )
        receipt = self.submission.attempt_ids[:1].response_xml
        self.assertTrue(certification_set.can_new_run)
        self.assertFalse(self.run._blocks_new_execution())
        with patch.object(CertificationAuthentication, "post") as network:
            action = certification_set.action_new_run()
            network.assert_not_called()
        fresh = self.env["l10n_cl.dte.certification.run"].browse(action["res_id"])
        self.assertEqual(fresh.revision, 2)
        self.assertEqual(fresh.state, "draft")
        self.assertEqual(len(fresh.line_ids), len(self.run.line_ids))
        self.assertTrue(all(fresh.line_ids.mapped("selected")))
        self.assertFalse(fresh.line_ids.move_id | self.run.line_ids.move_id)
        self.assertFalse(fresh.line_ids.document_id)
        self.assertFalse(fresh.envelope_submission_ids)
        self.assertFalse(fresh.envelope_file)
        self.assertEqual(
            original,
            (self.run.envelope_file, self.run.envelope_digest, self.run.input_digest),
        )
        self.assertEqual(self.submission.attempt_ids[:1].response_xml, receipt)
        self.assertEqual(self.submission.track_id, "12345")
        self.assertEqual(self.submission.certification_run_id, self.run)

    def test_new_execution_after_verified_acceptance_retains_old_run(self):
        self._assert_new_execution_preserves_original()

    def test_new_execution_after_verified_observations_retains_old_run(self):
        self._assert_new_execution_preserves_original(observations=1)

    def _import_additional_caf(self, certification_set, register_only=False):
        key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        fixture = SimpleNamespace(
            caf_public_numbers=key.public_key().public_numbers(),
            sii_test_key=key,
            caf_private_pem=key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            ).decode(),
            caf_public_pem=key.public_key()
            .public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode(),
        )
        content = CafTestCase.build_caf_xml(
            fixture, issuer_vat=self.company.vat, key_id="100"
        )
        if register_only:
            caf = self.env["l10n_cl.caf.authorization"].create(
                {
                    "name": "synthetic-existing-caf.xml",
                    "company_id": self.company.id,
                    "sii_environment": "certification",
                    "issuer_vat": self.company.vat,
                    "document_code": "33",
                    "folio_start": 100,
                    "folio_end": 103,
                    "date_from": etree.fromstring(content).findtext("CAF/DA/FA"),
                    "caf_file": base64.b64encode(content),
                }
            )
            caf.action_validate_authorization()
            return caf
        wizard = self.env["l10n_cl.dte.certification.caf.import.wizard"].create(
            {
                "set_id": certification_set.id,
                "caf_filename": "synthetic-additional-caf.xml",
                "caf_file": base64.b64encode(content),
            }
        )
        wizard.action_import()
        return certification_set.caf_line_ids.sorted("id")[-1:]

    def test_import_reuses_existing_caf_and_reports_already_linked_range(self):
        certification_set = self._prepare_new_execution_test()
        certification_set.action_new_run()
        caf = self._import_additional_caf(certification_set, register_only=True)
        original = (caf.caf_digest, caf.state, caf.write_date)
        wizard = self.env["l10n_cl.dte.certification.caf.import.wizard"].create(
            {
                "set_id": certification_set.id,
                "caf_filename": "renamed-existing-caf.xml",
                "caf_file": caf.caf_file,
            }
        )
        wizard.action_import()
        links = certification_set.caf_line_ids.filtered(lambda link: link.caf_id == caf)
        self.assertEqual(len(links), 1)
        self.assertEqual(links.role, "additional")
        result = wizard.action_import()
        self.assertEqual(result["tag"], "display_notification")
        self.assertIn("DTE 33", result["params"]["message"])
        self.assertIn("100", result["params"]["message"])
        self.assertIn("103", result["params"]["message"])
        self.assertEqual(original, (caf.caf_digest, caf.state, caf.write_date))
        self.assertEqual(len(certification_set.caf_line_ids), 1)

    def test_import_cannot_reactivate_an_existing_revoked_caf(self):
        certification_set = self._prepare_new_execution_test()
        certification_set.action_new_run()
        caf = self._import_additional_caf(certification_set, register_only=True)
        caf.action_revoke_authorization()
        wizard = self.env["l10n_cl.dte.certification.caf.import.wizard"].create(
            {
                "set_id": certification_set.id,
                "caf_filename": "revoked-caf.xml",
                "caf_file": caf.caf_file,
            }
        )
        with self.assertRaises(UserError):
            wizard.action_import()
        self.assertEqual(caf.state, "revoked")
        self.assertFalse(certification_set.caf_line_ids)

    def test_additional_caf_for_fresh_revision_preserves_sent_execution(self):
        certification_set = self._prepare_new_execution_test(observations=1)
        service = CertificationCaseGenerationService()
        snapshot = service._freeze_input(self.run)
        self.run._write_internal(
            {
                "input_snapshot": snapshot,
                "input_digest": CertificationSetParser().canonical_digest(snapshot),
            }
        )
        original = (
            self.run.input_snapshot,
            self.run.input_digest,
            self.run.envelope_file,
        )
        receipt = self.submission.attempt_ids[:1].response_xml
        action = certification_set.action_new_run()
        fresh = self.env["l10n_cl.dte.certification.run"].browse(action["res_id"])
        certification_set.action_open_caf_import_wizard()
        view = certification_set.get_view(
            self.env.ref("l10n_cl_dte_certification.certification_set_view_form").id,
            "form",
        )
        root = etree.fromstring(view["arch"])
        context = certification_set.with_context(bin_size=True).web_read(
            {"can_add_caf": {}, "can_edit_envelope": {}}
        )[0]
        button = root.xpath("//button[@name='action_open_caf_import_wizard']")[0]
        self.assertFalse(safe_eval(button.get("invisible"), context))
        caf_list = root.xpath("//field[@name='caf_line_ids']")[0]
        self.assertTrue(safe_eval(caf_list.get("readonly"), context))
        with patch.object(CertificationAuthentication, "post") as network:
            link = self._import_additional_caf(certification_set)
            network.assert_not_called()
        self.assertEqual(link.caf_id.state, "validated")
        self.assertEqual(link.caf_id._next_available_folio(), 100)
        self.assertEqual(service._freeze_input(self.run), snapshot)
        self.assertEqual(
            original,
            (self.run.input_snapshot, self.run.input_digest, self.run.envelope_file),
        )
        self.assertIn(
            link.id,
            [row["line_id"] for row in service._freeze_input(fresh)["caf_line_ids"]],
        )
        self.assertEqual(self.submission.attempt_ids[:1].response_xml, receipt)
        self.assertEqual(self.submission.track_id, "12345")
        with self.assertRaises(UserError):
            self.env["l10n_cl.dte.certification.set.caf"].create(
                {
                    "set_id": certification_set.id,
                    "caf_id": link.caf_id.id,
                    "role": "primary",
                }
            )
        with self.assertRaises(UserError):
            link.write({"sequence": 20})
        with self.assertRaises(UserError):
            link.unlink()

    def test_caf_replenishment_requires_clean_draft_and_verified_history(self):
        certification_set = self._prepare_new_execution_test()
        with self.assertRaises(UserError):
            certification_set.action_open_caf_import_wizard()
        action = certification_set.action_new_run()
        fresh = self.env["l10n_cl.dte.certification.run"].browse(action["res_id"])
        certification_set.action_open_caf_import_wizard()
        for values in [{"state": "generated"}, {"input_digest": "frozen"}]:
            with self.subTest(values=values), self.env.cr.savepoint():
                fresh._write_internal(values)
                with self.assertRaises(UserError):
                    certification_set.action_open_caf_import_wizard()
                fresh._write_internal({"state": "draft", "input_digest": False})
        self.submission._write_internal({"state": "outcome_unknown"})
        with self.assertRaises(UserError):
            certification_set.action_open_caf_import_wizard()

    def test_new_execution_remains_available_in_web_read_binary_size_context(self):
        certification_set = self._prepare_new_execution_test(observations=1)
        original_content = certification_set.source_file
        for context in [
            {"bin_size": True},
            {"bin_size_source_file": True},
        ]:
            with self.subTest(context=context):
                web_set = certification_set.with_context(**context)
                result = web_set.web_read(
                    {
                        "can_new_run": {},
                        "can_operate": {},
                        "source_file": {},
                    }
                )[0]
                self.assertTrue(result["can_new_run"])
                self.assertTrue(result["can_operate"])
                if context.get("bin_size"):
                    self.assertNotEqual(result["source_file"], original_content)
                self.assertIsNone(web_set._verify_source_integrity())

    def test_new_execution_after_verified_rejection_uses_a_new_run(self):
        self._assert_new_execution_preserves_original(rejected=1)

    def test_pending_or_uncertain_transmission_blocks_new_execution(self):
        certification_set = self._prepare_new_execution_test()
        for state in [
            "pending",
            "sending",
            "submitted",
            "processing",
            "outcome_unknown",
            "error",
        ]:
            with self.subTest(state=state):
                self.submission._write_internal({"state": state})
                self.assertFalse(certification_set.can_new_run)
                with self.assertRaisesRegex(UserError, "Confirm the SII result"):
                    certification_set.action_new_run()

    def test_export_without_sii_evidence_blocks_new_execution(self):
        certification_set = self._prepare_new_execution_test()
        corrected = base64.b64decode(self.run.envelope_file) + b"\n"
        self.run._write_internal(
            {
                "envelope_file": base64.b64encode(corrected),
                "envelope_digest": hashlib.sha256(corrected).hexdigest(),
            }
        )
        self.assertFalse(certification_set.can_new_run)
        with self.assertRaises(UserError):
            certification_set.action_new_run()

    def test_another_export_without_a_submission_blocks_a_new_execution(self):
        certification_set = self._prepare_new_execution_test()
        action = certification_set.action_new_run()
        fresh = self.env["l10n_cl.dte.certification.run"].browse(action["res_id"])
        fresh._write_internal({"state": "exported"})
        self.assertFalse(certification_set.can_new_run)
        with self.assertRaises(UserError):
            certification_set.action_new_run()

    def test_tampered_status_cannot_unlock_a_new_execution(self):
        certification_set = self._prepare_new_execution_test(observations=1)
        attempt = self.submission.attempt_ids[:1]
        attachment = self.env["ir.attachment"].search(
            [
                ("res_model", "=", attempt._name),
                ("res_id", "=", attempt.id),
                ("res_field", "=", "response_xml"),
            ]
        )
        attachment.write({"datas": base64.b64encode(b"<RESPUESTA/>")})
        attempt.invalidate_recordset(["response_xml"])
        self.assertTrue(self.run._blocks_new_execution())
        with self.assertRaises(UserError):
            certification_set.action_new_run()

    def test_status_with_wrong_document_count_blocks_a_new_execution(self):
        certification_set = self._prepare_new_execution_test()
        self._record_document_rejection(informed=2, rejected=0)
        self.submission._write_internal({"state": "accepted"})
        self.assertFalse(certification_set.can_new_run)
        with self.assertRaises(UserError):
            certification_set.action_new_run()

    def test_full_document_rejection_appends_signatures_without_rewriting_evidence(
        self,
    ):
        original = self._attach_signed_document(self.signed)
        self._record_document_rejection()
        previous = self.submission.request_xml
        self.assertTrue(self.run.can_correct_sii_envelope)
        self.run.action_correct_sii_envelope()
        current = original.document_id._get_signed_artifact()
        self.assertEqual(current.supersedes_id, original)
        self.assertEqual(original.content, base64.b64encode(self.signed))
        self.assertEqual(self.submission.request_xml, previous)
        self.assertEqual(self.submission.track_id, "12345")
        self.assertEqual(self.submission.state, "rejected")
        self.assertEqual(original.document_id.folio, 1)
        audit = self.run.signature_regeneration_ids.signature_changes[0]
        self.assertEqual(audit["receipts"][0]["code"], "EPR")
        self.assertEqual(
            audit["document_changes"][0]["previous_artifact_id"], original.id
        )
        self.assertTrue(self.run.can_prepare_sii_submission)

    def test_incomplete_or_partially_accepted_counts_cannot_unlock_correction(self):
        for informed, rejected in [(2, 2), (2, 1), (1, 0)]:
            with self.subTest(informed=informed, rejected=rejected):
                self._record_document_rejection(informed, rejected)
                self.assertFalse(self.run.can_correct_sii_envelope)
                with self.assertRaises(UserError):
                    self.run.action_correct_sii_envelope()

    def test_document_correction_adds_discount_amount_and_preserves_subtotal(self):
        root = etree.fromstring(self.signed)
        root.remove(root.xpath("./*[local-name()='Signature']")[0])
        doc = root[0]
        ns = "{http://www.sii.cl/SiiDte}"
        detail = etree.SubElement(doc, ns + "Detalle")
        for name, value in [
            ("NroLinDet", "1"),
            ("QtyItem", "299"),
            ("PrcItem", "6303"),
            ("DescuentoPct", "10"),
            ("MontoItem", "1696137"),
        ]:
            etree.SubElement(detail, ns + name).text = value
        signed = SignatureService().sign_document(
            self.company, "certification", etree.tostring(root)
        )
        original = self._attach_signed_document(signed)
        self._record_document_rejection()
        new, audit = RejectedCertificationDocumentCorrection().prepare(
            self.run.line_ids, signed, etree.fromstring(signed)
        )
        corrected = etree.fromstring(new)
        self.assertEqual(corrected.findtext(".//" + ns + "DescuentoMonto"), "188460")
        self.assertEqual(corrected.findtext(".//" + ns + "MontoItem"), "1696137")
        self.assertEqual(original.content, base64.b64encode(signed))
        self.assertEqual(
            audit["detail_changes"], [{"line": "1", "discount_amount": "188460"}]
        )
        SignatureService().verify_signature(new, sii_standalone=True)

    def test_document_correction_rejects_current_xml_different_from_sent_xml(self):
        original = self._attach_signed_document(self.signed)
        sent = etree.fromstring(self.signed)
        sent[0].set("ID", "DIFFERENT")
        with self.assertRaisesRegex(UserError, "retained evidence"):
            RejectedCertificationDocumentCorrection().prepare(
                self.run.line_ids, self.signed, sent
            )
        self.assertEqual(original.document_id._get_signed_artifact(), original)

    def test_unlinked_or_noncover_status_cannot_unlock_correction(self):
        for code, track in [("RCT", "999"), ("EPR", "12345"), ("RFR", "12345")]:
            with self.subTest(code=code, track=track), self.env.cr.savepoint():
                self._record_cover_rejection(code, track)
                self.assertFalse(self.run.can_correct_sii_envelope)
                with self.assertRaises(UserError):
                    self.run.action_correct_sii_envelope()

    def test_repeated_prepare_is_blocked_without_a_new_rejection(self):
        self.run.action_correct_sii_envelope()
        with self.assertRaises(UserError):
            self.run.action_correct_sii_envelope()
        self.assertEqual(len(self.run.signature_regeneration_ids), 1)

    def test_no_correction_for_track_accepted_or_unverifiable_receipt(self):
        for values in (
            {"track_id": "123"},
            {"state": "accepted"},
            {"state": "sending"},
        ):
            with self.subTest(values=values), self.env.cr.savepoint():
                self.submission._write_internal(values)
                self.assertFalse(self.run.can_correct_sii_envelope)
                with self.assertRaises(UserError):
                    self.run.action_correct_sii_envelope()
                self.submission._write_internal(
                    {"track_id": False, "state": "outcome_unknown"}
                )
        self.attempt._complete(
            "unknown", self.receipt.replace(b"012345678-5", b"76086428-5")
        )
        self.assertFalse(self.run.can_correct_sii_envelope)
        with self.assertRaises(UserError):
            self.run.action_correct_sii_envelope()

    def test_missing_or_tampered_receipt_does_not_unlock_resend(self):
        self.attempt._complete("unknown", b"<RECEPCIONDTE/>")
        self.assertFalse(self.run.can_correct_sii_envelope)
        self.assertFalse(self.run.can_prepare_sii_submission)

    def test_receipt_attachment_tampering_does_not_unlock_correction(self):
        attachment = self.env["ir.attachment"].search(
            [
                ("res_model", "=", "l10n_cl.sii.envelope.attempt"),
                ("res_id", "=", self.attempt.id),
                ("res_field", "=", "response_xml"),
            ]
        )
        self.assertEqual(len(attachment), 1)
        attachment.write({"datas": base64.b64encode(self.receipt + b" ")})
        self.attempt.invalidate_recordset(["response_xml"])
        self.run.invalidate_recordset()
        self.assertFalse(self.run.can_correct_sii_envelope)
        self.assertFalse(self.run.can_prepare_sii_submission)

    def test_certificate_selection_defaults_to_holder_instead_of_company(self):
        source = b"Synthetic certificate sender selection"
        fresh = self.env["l10n_cl.dte.certification.set"]._create_imported(
            {
                "name": "Sender selection",
                "company_id": self.company.id,
                "source_file": base64.b64encode(source),
                "source_filename": "sender.txt",
                "source_digest": hashlib.sha256(source).hexdigest(),
                "source_byte_count": len(source),
                "source_encoding": "utf-8",
                "parser_profile": "sii_txt_v1",
                "parser_version": "test",
                "attention_number": "sender-selection",
            }
        )
        fresh.write({"signing_certificate_id": self.profile.certificate_id.id})
        self.assertEqual(fresh.sender_vat, "12345678-5")
        self.assertNotEqual(fresh.sender_vat, self.company.vat)

    def test_archived_and_nonmanager_cannot_prepare_correction(self):
        with self.assertRaises(AccessError):
            self.run.with_user(self.dte_user).action_correct_sii_envelope()
        self.run.set_id.action_archive()
        self.assertFalse(self.run.can_correct_sii_envelope)
        with self.assertRaises(UserError):
            self.run.action_correct_sii_envelope()
