# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from lxml import etree

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, tagged

from odoo.addons.l10n_cl_dte_note.services.note_xml_composer import NoteXmlComposer

from ..services.case_generation_service import CertificationCaseGenerationService
from ..services.certification_note_adapter import CertificationNoteAdapter
from ..services.receiver_activity_correction import unresolved_source_instructions
from ..services.set_completeness_service import CertificationSetCompletenessService
from .common import DteCertificationTestCase
from .test_certification_set_reimport import SET_SOURCE
from .test_set_preflight import _OrderedRecords


@tagged("post_install", "-at_install")
class TestReceiverActivityCorrection(DteCertificationTestCase):
    def _import_set(self, *, with_source_text=False):
        source = SET_SOURCE
        if not with_source_text:
            source = source.split(b"DICE:")[0]
        importer = (
            self.env["l10n_cl.dte.certification.import.wizard"]
            .with_user(self.certification_manager)
            .create(
                {
                    "company_id": self.company.id,
                    "step": "select",
                    "source_file": base64.b64encode(source),
                    "source_filename": "synthetic-receiver-activity.txt",
                    "source_encoding": "utf-8",
                    "parser_profile": "sii_txt_v2",
                    "accounting_warning_acknowledged": True,
                }
            )
        )
        importer.action_import()
        return (
            self.env["l10n_cl.dte.certification.case"]
            .with_user(self.certification_manager)
            .search(
                [
                    ("set_id.company_id", "=", self.company.id),
                    ("case_identifier", "=", "12345-2"),
                    ("set_id.state", "!=", "archived"),
                ]
            )
        )

    def _wizard(self, case, **values):
        return (
            self.env["l10n_cl.dte.certification.receiver.activity.wizard"]
            .with_user(self.certification_manager)
            .create(
                {
                    "case_id": case.id,
                    "previous_activity": "Giro anterior de prueba",
                    "corrected_activity": "Giro corregido de prueba",
                    "verification_reference": "Synthetic unit test review",
                    **values,
                }
            )
        )

    def test_review_resolves_missing_text_and_preserves_source_and_amounts(self):
        case = self._import_set()
        self.assertEqual(case.readiness, "unsupported")
        certification_set = case.set_id
        before = (
            certification_set.source_file,
            certification_set.source_digest,
            deepcopy(certification_set.normalized_payload),
            certification_set.normalized_digest,
        )
        move_count = self.env["account.move"].search_count([])
        document_count = self.env["l10n_cl.electronic.document"].search_count([])
        partner_count = self.env["res.partner"].search_count([])

        action = case.action_correct_receiver_activity()
        with Form(
            self.env[action["res_model"]]
            .with_user(self.certification_manager)
            .with_context(**action["context"])
        ) as review:
            review.previous_activity = "Giro anterior de prueba"
            review.corrected_activity = "Giro corregido de prueba"
            review.verification_reference = "Synthetic unit test review"
        review.save().action_apply()

        self.assertEqual(case.readiness, "validated")
        self.assertFalse(case.diagnostics)
        self.assertEqual(case.reference_code, "2")
        self.assertEqual(case.reference_reason, "CORRIGE GIRO DEL RECEPTOR")
        self.assertEqual(
            case.sii_reference_reason,
            "Donde dice: Giro del receptor: Giro anterior de prueba\n"
            "Debe decir: Giro del receptor: Giro corregido de prueba",
        )
        self.assertEqual(
            case.receiver_activity_correction["actor_id"], self.certification_manager.id
        )
        self.assertTrue(case.receiver_activity_correction["applied_at"])
        self.assertEqual(
            case.completion_provenance["entries"][-1]["origin"], "user_input"
        )
        self.assertFalse(certification_set.contains_synthetic_completion)
        self.assertEqual(
            before,
            (
                certification_set.source_file,
                certification_set.source_digest,
                certification_set.normalized_payload,
                certification_set.normalized_digest,
            ),
        )
        self.assertEqual(self.env["account.move"].search_count([]), move_count)
        self.assertEqual(
            self.env["l10n_cl.electronic.document"].search_count([]), document_count
        )
        self.assertEqual(self.env["res.partner"].search_count([]), partner_count)
        findings = CertificationSetCompletenessService().analyze_set(
            certification_set, "official_sii"
        )
        codes = {
            finding["code"]
            for result in findings
            if result["case_id"] == case.id
            for finding in result["findings"]
        }
        self.assertNotIn("unsupported_instructions", codes)
        self.assertNotIn("missing_text_correction_content", codes)
        self.assertIn("missing_receiver", codes)
        detail = CertificationCaseGenerationService()._text_correction_detail(case)
        self.assertEqual(detail["unit_price"], "0")

    def test_review_does_not_hide_other_unsupported_instructions(self):
        case = self._import_set()
        self._wizard(case).action_apply()
        source = deepcopy(case._source_case())
        unknown = {"source_line": 12, "text": "Unknown instruction"}
        source["unsupported_instructions"].append(unknown)
        self.assertEqual(unresolved_source_instructions(case, source), [unknown])

    def test_review_preserves_generation_readiness_for_mapped_notes(self):
        case = self._import_set()
        case.set_id.case_ids.write({"partner_id": self.company.partner_id.id})
        self._wizard(case).action_apply()
        self.assertEqual(case.readiness, "ready")

    def test_source_text_cannot_be_replaced(self):
        case = self._import_set(with_source_text=True)
        original = case.sii_reference_reason
        self.assertFalse(case.can_correct_receiver_activity)
        with self.assertRaises(UserError):
            self._wizard(case).action_apply()
        self.assertEqual(case.sii_reference_reason, original)

    def test_invalid_values_are_rejected_through_rpc(self):
        case = self._import_set()
        for values in [
            {"previous_activity": " "},
            {"corrected_activity": "Giro anterior de prueba"},
            {"corrected_activity": "Giro\ncorregido"},
            {"corrected_activity": "Giro \U0001f680"},
            {"corrected_activity": "x" * 401},
            {"verification_reference": " "},
        ]:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self._wizard(case, **values).action_apply()
        self.assertFalse(case.sii_reference_reason)

    def test_archived_case_cannot_be_completed(self):
        case = self._import_set()
        wizard = self._wizard(case)
        case.set_id.action_archive()
        with self.assertRaises(UserError):
            wizard.action_apply()

    def test_existing_execution_freezes_the_review(self):
        case = self._import_set()
        wizard = self._wizard(case)
        self.env["l10n_cl.dte.certification.run"].with_user(
            self.certification_manager
        )._create_for_set(case.set_id, revision=1)
        with self.assertRaises(UserError):
            wizard.action_apply()

    def test_unrelated_case_cannot_be_completed(self):
        case = self._import_set()
        invoice = case.set_id.case_ids.filtered(lambda item: item.document_code == "33")
        with self.assertRaises(UserError):
            self._wizard(invoice).action_apply()

    def test_review_is_manager_only_and_fields_are_protected(self):
        case = self._import_set()
        with self.assertRaises(AccessError):
            self._wizard(case).with_user(self.dte_user).action_apply()
        with self.assertRaises(UserError):
            case.write({"sii_reference_reason": "Bypass"})
        with self.assertRaises(UserError):
            case.write({"receiver_activity_correction": {"origin": "user_input"}})

    def test_repeated_review_keeps_previous_provenance(self):
        case = self._import_set()
        self._wizard(case).action_apply()
        self._wizard(case, corrected_activity="Otra actividad corregida").action_apply()
        self.assertEqual(len(case.completion_provenance["entries"]), 2)
        action = case.action_correct_receiver_activity()
        self.assertEqual(
            action["context"]["default_corrected_activity"], "Otra actividad corregida"
        )

    def test_reviewed_text_reaches_the_zero_value_note_xml(self):
        case = self._import_set()
        self._wizard(case).action_apply()
        target_document = SimpleNamespace(document_code="33", folio=1)
        target = SimpleNamespace(
            document_id=target_document,
            case_id=case.set_id.case_ids.filtered(
                lambda item: item.case_identifier == case.reference_case_identifier
            ),
        )
        run = SimpleNamespace(
            set_id=case.set_id,
            revision=1,
            input_digest="synthetic-input",
            issue_date=date(2026, 11, 5),
            line_ids=_OrderedRecords([target]),
        )
        line = SimpleNamespace(case_id=case, run_id=run)
        move = SimpleNamespace(
            env=self.env,
            invoice_line_ids=_OrderedRecords([]),
            amount_tax=0,
            amount_total=0,
            company_id=self.company,
            partner_id=self.company.partner_id,
            invoice_date=run.issue_date,
        )
        party = {
            "vat": "12345678-5",
            "name": "Synthetic unit test party",
            "activity": "Actividad de prueba",
            "activity_code": "123456",
        }
        with (
            patch.object(CertificationNoteAdapter, "_check_case", return_value=line),
            patch(
                "odoo.addons.l10n_cl_dte_certification.services."
                "certification_note_adapter.InvoiceSnapshotService._snapshot_lines",
                return_value=([{"name": "Text", "subtotal": "0"}], 0, 0),
            ),
            patch(
                "odoo.addons.l10n_cl_dte_certification.services."
                "certification_note_adapter.InvoiceSnapshotService._partner_snapshot",
                return_value=party,
            ),
            patch(
                "odoo.addons.l10n_cl_dte_certification.services."
                "certification_note_adapter.NoteReferenceService.read_original_snapshot",
                return_value={"issue_date": "2026-11-05"},
            ),
        ):
            snapshot = CertificationNoteAdapter().capture_snapshot(move, None, 2, "61")
        # Exercise serialization without generating a CAF, signing or sending.
        snapshot["ted_xml"] = b'<TED version="1.0"/>'
        root = etree.fromstring(NoteXmlComposer().compose_unsigned_document(snapshot))
        ns = {"s": "http://www.sii.cl/SiiDte"}
        self.assertEqual(
            root.findtext(".//s:DscItem", namespaces=ns), case.sii_reference_reason
        )
        self.assertEqual(root.findtext(".//s:CodRef", namespaces=ns), "2")
        self.assertEqual(
            root.findall(".//s:RazonRef", namespaces=ns)[1].text, case.reference_reason
        )
        self.assertEqual(root.findtext(".//s:MontoItem", namespaces=ns), "0")
        self.assertEqual(root.findtext(".//s:MntTotal", namespaces=ns), "0")
        self.assertIsNone(root.find(".//s:QtyItem", namespaces=ns))
        self.assertIsNone(root.find(".//s:PrcItem", namespaces=ns))

    def test_execution_snapshot_includes_the_review(self):
        case = self._import_set()
        self._wizard(case).action_apply()
        run = (
            self.env["l10n_cl.dte.certification.run"]
            .with_user(self.certification_manager)
            ._create_for_set(case.set_id, revision=1)
        )
        profile = MagicMock()
        profile.id = 123
        profile.certificate_id._get_fingerprint_bytes.return_value = b"synthetic"
        with patch.object(
            CertificationCaseGenerationService, "_signing_profile", return_value=profile
        ):
            snapshot = CertificationCaseGenerationService()._freeze_input(run)
        frozen_case = next(
            item
            for item in snapshot["cases"]
            if item["case_identifier"] == case.case_identifier
        )
        self.assertEqual(
            frozen_case["receiver_activity_correction"],
            case.receiver_activity_correction,
        )
        self.assertEqual(frozen_case["sii_reference_reason"], case.sii_reference_reason)
