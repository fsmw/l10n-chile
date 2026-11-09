# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
from contextlib import ExitStack
from unittest.mock import patch

from lxml import etree

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import file_path
from odoo.tools.translate import PoFileReader

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_signing.tests.common import SigningTestCase
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import DteXmlComposer

from ..services.set_execution_service import CertificationSetExecutionService
from .common import DteCertificationTestCase


@tagged("post_install", "-at_install")
class TestSignatureRegeneration(DteCertificationTestCase, SigningTestCase):
    def test_native_translation_catalog_includes_action_and_history(self):
        # Odoo merges PO with POT before loading, so a stale template can hide
        # valid PO entries even though gettext syntax checks pass.
        rows = list(PoFileReader(file_path("l10n_cl_dte_certification/i18n/es_CL.po")))
        terms = {row["src"]: row["value"] for row in rows}
        self.assertEqual(terms.get("Regenerate Signatures"), "Regenerar firmas")
        self.assertEqual(terms.get("Signature History"), "Historial de firmas")

    def setUp(self):
        super().setUp()
        self.env = self.env(user=self.certification_manager)
        self.company = self.company.with_env(self.env)
        self.profile = self.create_signing_profile(environment="certification")
        source = b"Synthetic signature recovery test"
        certification_set = self.env["l10n_cl.dte.certification.set"]._create_imported(
            {
                "name": "Signature recovery",
                "company_id": self.company.id,
                "source_file": base64.b64encode(source),
                "source_filename": "test.txt",
                "source_digest": hashlib.sha256(source).hexdigest(),
                "source_byte_count": len(source),
                "source_encoding": "utf-8",
                "parser_profile": "sii_txt_v1",
                "parser_version": "test",
                "attention_number": "signature-recovery",
            }
        )
        for sequence in (1, 2):
            self.env["l10n_cl.dte.certification.case"]._create_imported(
                {
                    "set_id": certification_set.id,
                    "sequence": sequence,
                    "case_identifier": f"signature-recovery-{sequence}",
                    "document_code": "33",
                    "source_start_line": sequence,
                    "source_end_line": sequence,
                    "instruction_text": "Synthetic case",
                }
            )
        certification_set._write_internal({"state": "ready"})
        self.run = self.env["l10n_cl.dte.certification.run"]._create_for_set(
            certification_set, 1
        )
        self.originals = self.env["l10n_cl.electronic.artifact"]
        for line in self.run.line_ids:
            document = self.create_document()
            caf = self.create_artifact(document, "caf_source")
            document.action_record_transition(
                "authorized", "test_caf", artifact_id=caf.id
            )
            document.action_record_transition(
                "numbered",
                "test_folio",
                artifact_id=caf.id,
                folio_number=line.sequence,
            )
            snapshot = self.create_artifact(document, "source_snapshot", b"{}")
            document.action_record_source_snapshot(snapshot.id)
            unsigned = (
                '<DTE xmlns="http://www.sii.cl/SiiDte" version="1.0">'
                f'<Documento ID="DTE-{line.sequence}"><Monto>123</Monto>'
                "</Documento></DTE>"
            ).encode()
            prepared = self.create_artifact(document, "unsigned_xml", unsigned)
            document.action_record_transition(
                "prepared", "test_prepared", artifact_id=prepared.id
            )
            signed = SignatureService().sign_document(
                self.company, "certification", unsigned
            )
            root = etree.fromstring(signed)
            root.find("{http://www.w3.org/2000/09/xmldsig#}Signature")[
                1
            ].text = "broken"
            old = self.create_artifact(document, "signed_xml", etree.tostring(root))
            document.action_record_transition(
                "signed", "test_signed", artifact_id=old.id
            )
            self.originals |= old.with_env(self.env)
            line._write_internal(
                {"document_id": document.id, "selected": True, "state": "prepared"}
            )
        self.old_envelope = base64.b64encode(b"original envelope")
        self.run._write_internal(
            {
                "state": "exported",
                "envelope_file": self.old_envelope,
                "envelope_filename": "original.xml",
                "envelope_digest": hashlib.sha256(b"original envelope").hexdigest(),
            }
        )
        # Small XML fixtures exercise real signing and ORM audit/rollback, while
        # SET fiscal/schema coverage is exercised separately by execution tests.
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(
            patch.object(type(self.run), "_check_execution_access", return_value=True)
        )
        self.stack.enter_context(
            patch.object(type(self.run), "_run_official_preflight", return_value={})
        )
        self.stack.enter_context(
            patch.object(
                CertificationSetExecutionService,
                "validate_case_result",
                return_value=b"",
            )
        )
        self.stack.enter_context(
            patch.object(DteXmlComposer, "validate_document", return_value=True)
        )
        self.stack.enter_context(
            patch.object(
                CertificationSetExecutionService, "build_envelope", self._build_envelope
            )
        )

    @staticmethod
    def _build_envelope(service, run):
        run._write_internal(
            {
                "state": "exported",
                "envelope_file": base64.b64encode(b"replacement envelope"),
                "envelope_filename": "replacement.xml",
                "envelope_digest": hashlib.sha256(b"replacement envelope").hexdigest(),
            }
        )

    def test_regeneration_preserves_fiscal_data_and_all_previous_evidence(self):
        before = {artifact.id: artifact.content for artifact in self.originals}
        documents = self.run.line_ids.document_id
        folios = documents.mapped("folio")
        snapshots = documents.mapped("payload_digest")
        self.assertTrue(self.run.can_regenerate_signatures)

        action = self.run.action_regenerate_signatures()

        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(documents.mapped("folio"), folios)
        self.assertEqual(documents.mapped("payload_digest"), snapshots)
        for document in documents:
            current = document._get_signed_artifact()
            self.assertIn(current.supersedes_id, self.originals)
            self.assertEqual(
                current.supersedes_id.content, before[current.supersedes_id.id]
            )
            self.assertTrue(
                SignatureService().verify_signature(base64.b64decode(current.content))[
                    "valid"
                ]
            )
            self.assertEqual(document.preparation_state, "signed")
            self.assertEqual(
                document.event_ids.filtered(
                    lambda event: event.event_code == "signature_regenerated"
                ).artifact_id,
                current,
            )
        history = self.run.signature_regeneration_ids
        self.assertEqual(len(history), 1)
        self.assertEqual(history.previous_envelope_file, self.old_envelope)
        self.assertEqual(history.replacement_envelope_file, self.run.envelope_file)
        self.assertEqual(history.actor_id, self.certification_manager)
        self.assertEqual(history.certificate_id, self.profile.certificate_id)
        self.assertEqual(len(history.signature_changes), 2)
        with self.assertRaises(AccessError):
            history.write({"previous_envelope_digest": "changed"})
        with self.assertRaises(AccessError):
            history.unlink()

    def test_failure_rolls_back_every_signature_and_the_envelope(self):
        artifacts = self.run.line_ids.document_id.artifact_ids
        original_sign = SignatureService.sign_document
        calls = []

        def fail_second(service, *args):
            calls.append(True)
            if len(calls) == 2:
                message = "Synthetic second signature failure"
                raise ValidationError(message)
            return original_sign(service, *args)

        with (
            patch.object(SignatureService, "sign_document", fail_second),
            self.assertRaises(ValidationError),
        ):
            self.run.action_regenerate_signatures()

        self.assertEqual(self.run.line_ids.document_id.artifact_ids, artifacts)
        self.assertEqual(self.run.envelope_file, self.old_envelope)
        self.assertFalse(self.run.signature_regeneration_ids)
        self.assertFalse(
            self.run.line_ids.document_id.event_ids.filtered(
                lambda event: event.event_code == "signature_regenerated"
            )
        )

    def test_queued_or_attempted_envelope_blocks_regeneration(self):
        self.run._write_internal({"reportability_state": "ready"})
        submission = self.env["l10n_cl.sii.envelope.submission"]._create_for_run(
            self.run
        )
        for state in (
            "pending",
            "sending",
            "outcome_unknown",
            "submitted",
            "rejected",
            "error",
        ):
            with self.subTest(state=state):
                submission._write_internal({"state": state})
                self.assertFalse(self.run.can_regenerate_signatures)
                with self.assertRaises(UserError):
                    self.run.action_regenerate_signatures()
        self.assertFalse(self.run.signature_regeneration_ids)

    def test_individual_submission_blocks_regeneration(self):
        document = self.run.line_ids[:1].document_id
        self.env["l10n_cl.electronic.submission"].create(
            {
                "document_id": document.id,
                "idempotency_key": "signature-test",
            }
        )
        with self.assertRaises(UserError):
            self.run.action_regenerate_signatures()
        self.assertFalse(self.run.signature_regeneration_ids)

    def test_envelope_failure_rolls_back_signature_revisions(self):
        artifacts = self.run.line_ids.document_id.artifact_ids
        with (
            patch.object(
                CertificationSetExecutionService,
                "build_envelope",
                side_effect=ValidationError("Synthetic envelope validation failure"),
            ),
            self.assertRaises(ValidationError),
        ):
            self.run.action_regenerate_signatures()
        self.assertEqual(self.run.line_ids.document_id.artifact_ids, artifacts)
        self.assertEqual(self.run.envelope_file, self.old_envelope)
        self.assertFalse(self.run.signature_regeneration_ids)

    def test_missing_prepared_case_blocks_regeneration(self):
        self.run.line_ids[:1]._write_internal({"document_id": False})
        self.assertFalse(self.run.can_regenerate_signatures)
        with self.assertRaises(UserError):
            self.run.action_regenerate_signatures()
        self.assertFalse(self.run.signature_regeneration_ids)

    def test_archived_set_signatures_are_immutable(self):
        self.run.set_id._write_internal({"state": "archived"})
        self.assertFalse(self.run.can_regenerate_signatures)
        with self.assertRaises(UserError):
            self.run.action_regenerate_signatures()
        self.assertFalse(self.run.signature_regeneration_ids)
        self.assertEqual(self.run.envelope_file, self.old_envelope)

    def test_fiscal_payload_change_rolls_back(self):
        original_sign = SignatureService.sign_document

        def change_payload(service, company, environment, unsigned):
            return original_sign(
                service, company, environment, unsigned.replace(b">123<", b">999<")
            )

        with (
            patch.object(SignatureService, "sign_document", change_payload),
            self.assertRaises(ValidationError),
        ):
            self.run.action_regenerate_signatures()
        self.assertFalse(self.run.signature_regeneration_ids)
        self.assertEqual(self.run.envelope_file, self.old_envelope)

    def test_signature_chain_and_history_are_append_only(self):
        self.run.action_regenerate_signatures()
        first = self.run.line_ids[:1].document_id._get_signed_artifact()
        self.run.action_regenerate_signatures()
        current = first.document_id._get_signed_artifact()
        self.assertEqual(current.supersedes_id, first)
        self.assertEqual(len(self.run.signature_regeneration_ids), 2)
        with self.assertRaises(ValidationError):
            self.env["l10n_cl.electronic.artifact"]._create_signature_revision(
                first, b"stale", "test"
            )
        with self.assertRaises(AccessError):
            first.write({"content": base64.b64encode(b"changed")})
        with self.assertRaises(AccessError):
            first.copy()

    def test_dte_operator_cannot_regenerate(self):
        with self.assertRaises(AccessError):
            self.run.with_user(self.dte_user).action_regenerate_signatures()

    def test_dte_manager_can_regenerate_without_certificate_administration(self):
        self.assertFalse(self.dte_manager.has_group("base.group_system"))
        self.run.with_user(self.dte_manager).action_regenerate_signatures()
        history = self.run.with_user(self.dte_manager).signature_regeneration_ids
        self.assertEqual(history.actor_id, self.dte_manager)
        self.assertTrue(history.certificate_name)
        self.assertTrue(history.certificate_fingerprint)

    def test_public_artifact_create_cannot_forge_a_signature_successor(self):
        with self.assertRaises(AccessError):
            self.env["l10n_cl.electronic.artifact"].create(
                {
                    "document_id": self.originals[:1].document_id.id,
                    "role": "signed_xml",
                    "supersedes_id": self.originals[:1].id,
                    "content": base64.b64encode(b"forged"),
                    "mimetype": "application/xml",
                }
            )
