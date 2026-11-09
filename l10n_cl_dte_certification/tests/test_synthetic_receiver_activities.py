# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from lxml import etree

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_account.services.dte_issuance_adapter import (
    InvoiceIssuanceAdapter,
)
from odoo.addons.l10n_cl_dte_note.services.note_xml_composer import NoteXmlComposer

from ..services.case_generation_service import CertificationCaseGenerationService
from ..services.certification_invoice_adapter import CertificationInvoiceAdapter
from ..services.certification_note_adapter import CertificationNoteAdapter
from ..services.completion_policy import catalogue_activity_matches_xml
from ..services.official_set_preflight import OfficialCertificationSetPreflight
from ..services.receiver_activity_correction import correction_text
from ..services.set_completeness_service import CertificationSetCompletenessService
from ..services.sii_activity_catalogue import PROFILE, SiiActivityCatalogue
from ..services.synthetic_receiver_activity_service import (
    SyntheticReceiverActivityService,
    apply_receiver_activity_to_snapshot,
)
from .common import DteCertificationTestCase
from .test_certification_set_reimport import SET_SOURCE
from .test_set_preflight import _OrderedRecords


@tagged("post_install", "-at_install")
class TestSyntheticReceiverActivities(DteCertificationTestCase):
    def _import_set(self, with_source_text=False):
        source = SET_SOURCE if with_source_text else SET_SOURCE.split(b"DICE:")[0]
        source += (
            b"CASO 12345-3\nDOCUMENTO NOTA DE DEBITO ELECTRONICA\n"
            b"REFERENCIA NOTA DE CREDITO ELECTRONICA CORRESPONDIENTE A CASO 12345-2\n"
            b"RAZON REFERENCIA: ANULA NOTA DE CREDITO ELECTRONICA\n"
        )
        wizard = (
            self.env["l10n_cl.dte.certification.import.wizard"]
            .with_user(self.certification_manager)
            .create(
                {
                    "company_id": self.company.id,
                    "step": "select",
                    "source_file": base64.b64encode(source),
                    "source_filename": "synthetic-receiver-activities.txt",
                    "source_encoding": "utf-8",
                    "parser_profile": "sii_txt_v2",
                    "accounting_warning_acknowledged": True,
                }
            )
        )
        wizard.action_import()
        return (
            self.env["l10n_cl.dte.certification.set"]
            .with_user(self.certification_manager)
            .search(
                [
                    ("company_id", "=", self.company.id),
                    ("attention_number", "=", "12345"),
                    ("state", "!=", "archived"),
                ]
            )
        )

    def test_catalogue_has_public_codes_and_complete_xml_sized_choices(self):
        catalogue = SiiActivityCatalogue()
        rows = catalogue.activities()
        self.assertEqual(len(rows), 674)
        self.assertEqual(rows[0]["code"], "011101")
        self.assertEqual(len({row["code"] for row in rows}), 674)
        choices = catalogue.eligible_activities()
        self.assertGreater(len(choices), 2)
        self.assertTrue(all(len(row["name"]) <= 40 for row in choices))
        self.assertTrue(
            all(catalogue.matches(row["code"], row["name"]) for row in choices)
        )
        selected = catalogue.select("stable-unit-test")
        self.assertEqual(selected, catalogue.select("stable-unit-test"))
        self.assertNotEqual(
            selected["code"],
            catalogue.select("stable-unit-test", selected["code"])["code"],
        )

    def test_population_completes_existing_case_and_keeps_original_source(self):
        certification_set = self._import_set()
        invoice, note, debit = certification_set.case_ids.sorted("sequence")
        ids = certification_set.case_ids.ids
        before = (
            certification_set.source_file,
            certification_set.source_digest,
            deepcopy(certification_set.normalized_payload),
            certification_set.normalized_digest,
        )
        partner_activity = self.company.partner_id.l10n_cl_activity_description
        moves = self.env["account.move"].search_count([])
        folios = self.env["l10n_cl.caf.folio"].search_count([])
        documents = self.env["l10n_cl.electronic.document"].search_count([])

        note.action_generate_test_receiver_activities()

        self.assertEqual(certification_set.case_ids.ids, ids)
        self.assertEqual(note.readiness, "validated")
        self.assertFalse(note.diagnostics)
        self.assertEqual(certification_set.completion_mode, "official_sii")
        self.assertTrue(certification_set.contains_synthetic_completion)
        original = invoice.synthetic_receiver_activity
        corrected = note.synthetic_receiver_activity
        self.assertNotEqual(original["code"], corrected["code"])
        self.assertEqual(debit.synthetic_receiver_activity["code"], original["code"])
        self.assertEqual(
            note.sii_reference_reason,
            correction_text(original["name"], corrected["name"]),
        )
        self.assertEqual(note.reference_reason, "CORRIGE GIRO DEL RECEPTOR")
        self.assertEqual(note.reference_code, "2")
        self.assertEqual(note.receiver_activity_correction["profile"], PROFILE)
        self.assertEqual(
            note.receiver_activity_correction["actor_id"], self.certification_manager.id
        )
        self.assertEqual(
            before,
            (
                certification_set.source_file,
                certification_set.source_digest,
                certification_set.normalized_payload,
                certification_set.normalized_digest,
            ),
        )
        self.assertEqual(
            self.company.partner_id.l10n_cl_activity_description, partner_activity
        )
        self.assertEqual(self.env["account.move"].search_count([]), moves)
        self.assertEqual(self.env["l10n_cl.caf.folio"].search_count([]), folios)
        self.assertEqual(
            self.env["l10n_cl.electronic.document"].search_count([]), documents
        )

    def test_repeated_generation_does_not_append_duplicate_provenance(self):
        certification_set = self._import_set()
        certification_set.action_generate_test_receiver_activities()
        before = certification_set.case_ids.read(
            [
                "synthetic_receiver_activity",
                "receiver_activity_correction",
                "completion_provenance",
                "sii_reference_reason",
            ]
        )
        certification_set.action_generate_test_receiver_activities()
        self.assertEqual(
            certification_set.case_ids.read(
                [
                    "synthetic_receiver_activity",
                    "receiver_activity_correction",
                    "completion_provenance",
                    "sii_reference_reason",
                ]
            ),
            before,
        )

    def test_review_proposes_catalogue_values_without_writing(self):
        certification_set = self._import_set()
        note = certification_set.case_ids[1]
        plan = SyntheticReceiverActivityService().build_plan(certification_set)
        analysis = CertificationSetCompletenessService().analyze_set(
            certification_set, "local_synthetic"
        )
        finding = next(
            finding
            for result in analysis
            if result["case_id"] == note.id
            for finding in result["findings"]
            if finding["code"] == "missing_text_correction_content"
        )
        self.assertEqual(finding["profile"], PROFILE)
        self.assertEqual(
            finding["proposed_value"],
            SyntheticReceiverActivityService().proposed_text(note),
        )
        self.assertFalse(note.sii_reference_reason)
        self.assertFalse(note.synthetic_receiver_activity)
        self.assertEqual(
            plan, SyntheticReceiverActivityService().build_plan(certification_set)
        )

    def test_local_review_can_apply_the_automatic_proposal(self):
        certification_set = self._import_set()
        wizard = (
            self.env["l10n_cl.dte.certification.completion.wizard"]
            .with_user(self.certification_manager)
            .create({"set_id": certification_set.id, "target_mode": "local_synthetic"})
        )
        wizard.line_ids.filtered(
            lambda line: line.diagnostic_code == "missing_text_correction_content"
        ).write({"user_confirmed": True})
        wizard.action_apply_local_completion()
        self.assertEqual(certification_set.case_ids[1].readiness, "validated")

    def test_sii_text_and_its_referenced_chain_are_preserved(self):
        certification_set = self._import_set(with_source_text=True)
        text = certification_set.case_ids[1].sii_reference_reason
        with self.assertRaises(UserError):
            certification_set.action_generate_test_receiver_activities()
        self.assertEqual(certification_set.case_ids[1].sii_reference_reason, text)
        self.assertFalse(
            any(certification_set.case_ids.mapped("synthetic_receiver_activity"))
        )

    def test_manual_text_and_its_referenced_chain_are_preserved(self):
        certification_set = self._import_set()
        note = certification_set.case_ids[1]
        note._apply_receiver_activity_correction(
            "Actividad A", "Actividad B", "Test review"
        )
        with self.assertRaises(UserError):
            certification_set.action_generate_test_receiver_activities()
        self.assertEqual(note.receiver_activity_correction["origin"], "user_input")

    def test_archive_and_execution_freeze_generated_data(self):
        certification_set = self._import_set()
        run = (
            self.env["l10n_cl.dte.certification.run"]
            .with_user(self.certification_manager)
            ._create_for_set(certification_set, revision=1)
        )
        with self.assertRaises(UserError):
            certification_set.action_generate_test_receiver_activities()
        run._write_internal({"state": "blocked"})
        certification_set.action_archive()
        with self.assertRaises(UserError):
            certification_set.action_generate_test_receiver_activities()

    def test_generation_is_manager_only_and_json_cannot_be_written_directly(self):
        certification_set = self._import_set()
        with self.assertRaises(AccessError):
            certification_set.with_user(
                self.dte_user
            ).action_generate_test_receiver_activities()
        with self.assertRaises(UserError):
            certification_set.case_ids[0].write({"synthetic_receiver_activity": {}})

    def test_invoice_adapter_uses_the_generated_activity_in_its_snapshot(self):
        certification_set = self._import_set()
        certification_set.action_generate_test_receiver_activities()
        invoice = certification_set.case_ids[0]
        run = SimpleNamespace(
            set_id=certification_set,
            revision=1,
            input_digest="test",
            issue_date=date(2026, 11, 5),
        )
        line = SimpleNamespace(case_id=invoice, run_id=run)
        original = {"receiver": {"activity": "Unchanged partner value"}, "lines": []}
        with (
            patch.object(CertificationInvoiceAdapter, "_check_case", return_value=line),
            patch.object(
                InvoiceIssuanceAdapter,
                "capture_snapshot",
                return_value=deepcopy(original),
            ),
        ):
            snapshot = CertificationInvoiceAdapter().capture_snapshot(
                None, None, 1, "33"
            )
        self.assertEqual(
            snapshot["receiver"]["activity"],
            invoice.synthetic_receiver_activity["name"],
        )
        self.assertEqual(
            snapshot["certification"]["synthetic_receiver_activity"],
            invoice.synthetic_receiver_activity,
        )
        self.assertEqual(original["receiver"]["activity"], "Unchanged partner value")

    def test_snapshot_rejects_an_activity_without_the_synthetic_mark(self):
        certification_set = self._import_set()
        certification_set.action_generate_test_receiver_activities()
        invoice = certification_set.case_ids[0]
        certification_set._write_internal({"contains_synthetic_completion": False})
        with self.assertRaises(UserError):
            apply_receiver_activity_to_snapshot(
                {"receiver": {}, "certification": {}}, invoice
            )

    def _capture_note_snapshot(self, case, target_case):
        target = SimpleNamespace(
            document_id=SimpleNamespace(
                document_code=target_case.document_code, folio=1
            ),
            case_id=target_case,
        )
        run = SimpleNamespace(
            set_id=case.set_id,
            revision=1,
            input_digest="synthetic-input",
            issue_date=date(2026, 11, 5),
            line_ids=_OrderedRecords([target]),
        )
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
            "activity": "Unchanged master data",
            "activity_code": "123456",
        }
        with (
            patch.object(
                CertificationNoteAdapter,
                "_check_case",
                return_value=SimpleNamespace(case_id=case, run_id=run),
            ),
            patch(
                "odoo.addons.l10n_cl_dte_certification.services."
                "certification_note_adapter.InvoiceSnapshotService._snapshot_lines",
                return_value=([{"name": "Text", "subtotal": "0"}], 0, 0),
            ),
            patch(
                "odoo.addons.l10n_cl_dte_certification.services."
                "certification_note_adapter.InvoiceSnapshotService._partner_snapshot",
                return_value=dict(party),
            ),
            patch(
                "odoo.addons.l10n_cl_dte_certification.services."
                "certification_note_adapter.NoteReferenceService.read_original_snapshot",
                return_value={
                    "issue_date": "2026-11-05",
                    "lines": [{"description": target_case.sii_reference_reason}],
                },
            ),
        ):
            return CertificationNoteAdapter().capture_snapshot(
                move, None, 2, case.document_code
            )

    def test_note_and_its_cancellation_serialize_consistent_receiver_activities(self):
        certification_set = self._import_set()
        certification_set.action_generate_test_receiver_activities()
        invoice, note, debit = certification_set.case_ids.sorted("sequence")
        credit_snapshot = self._capture_note_snapshot(note, invoice)
        debit_snapshot = self._capture_note_snapshot(debit, note)
        self.assertEqual(
            credit_snapshot["receiver"]["activity"],
            note.synthetic_receiver_activity["name"],
        )
        self.assertEqual(
            debit_snapshot["receiver"]["activity"],
            invoice.synthetic_receiver_activity["name"],
        )
        for snapshot, reference_code in [(credit_snapshot, "2"), (debit_snapshot, "1")]:
            snapshot["ted_xml"] = b'<TED version="1.0"/>'
            root = etree.fromstring(
                NoteXmlComposer().compose_unsigned_document(snapshot)
            )
            ns = {"s": "http://www.sii.cl/SiiDte"}
            self.assertEqual(
                root.findtext(".//s:GiroRecep", namespaces=ns),
                snapshot["receiver"]["activity"],
            )
            self.assertEqual(
                root.findtext(".//s:DscItem", namespaces=ns), note.sii_reference_reason
            )
            self.assertEqual(
                root.findtext(".//s:CodRef", namespaces=ns), reference_code
            )
            self.assertEqual(root.findtext(".//s:MntTotal", namespaces=ns), "0")

    def test_execution_freezes_the_selected_codes_and_catalogue_identity(self):
        certification_set = self._import_set()
        certification_set.action_generate_test_receiver_activities()
        run = (
            self.env["l10n_cl.dte.certification.run"]
            .with_user(self.certification_manager)
            ._create_for_set(certification_set, revision=1)
        )
        profile = SimpleNamespace(
            id=123,
            certificate_id=SimpleNamespace(
                _get_fingerprint_bytes=lambda **kwargs: b"synthetic"
            ),
        )
        with patch.object(
            CertificationCaseGenerationService, "_signing_profile", return_value=profile
        ):
            snapshot = CertificationCaseGenerationService()._freeze_input(run)
        for case, frozen in zip(
            certification_set.case_ids.sorted("sequence"),
            snapshot["cases"],
            strict=True,
        ):
            self.assertEqual(
                frozen["synthetic_receiver_activity"], case.synthetic_receiver_activity
            )
            self.assertEqual(
                frozen["synthetic_receiver_activity"]["catalogue_digest"],
                SiiActivityCatalogue().provenance()["catalogue_digest"],
            )

    def _completion_diagnostics(self, certification_set):
        return OfficialCertificationSetPreflight._synthetic_completion_diagnostics(
            certification_set
        )

    def test_catalogue_completions_are_permitted_in_official_certification(self):
        certification_set = self._import_set()
        issuer_activity = self.company.dte_sii_activity_code
        certification_set.action_generate_test_receiver_activities()
        self.assertEqual(certification_set.completion_mode, "official_sii")
        self.assertTrue(certification_set.contains_synthetic_completion)
        self.assertEqual(self._completion_diagnostics(certification_set), [])
        self.assertEqual(self.company.dte_sii_activity_code, issuer_activity)

    def test_official_review_can_generate_catalogue_data_without_local_mode(self):
        certification_set = self._import_set()
        wizard = (
            self.env["l10n_cl.dte.certification.completion.wizard"]
            .with_user(self.certification_manager)
            .create({"set_id": certification_set.id, "target_mode": "official_sii"})
        )
        wizard.action_generate_test_receiver_activities()
        self.assertEqual(certification_set.completion_mode, "official_sii")
        self.assertEqual(self._completion_diagnostics(certification_set), [])

    def test_previously_local_catalogue_set_can_be_reviewed_for_certification(self):
        certification_set = self._import_set()
        certification_set._populate_test_receiver_activities(
            target_mode="local_synthetic"
        )
        before = certification_set.case_ids.mapped("completion_provenance")
        self.assertTrue(self._completion_diagnostics(certification_set))
        wizard = (
            self.env["l10n_cl.dte.certification.completion.wizard"]
            .with_user(self.certification_manager)
            .create({"set_id": certification_set.id, "target_mode": "official_sii"})
        )
        wizard.action_apply_official_review()
        self.assertEqual(certification_set.completion_mode, "official_sii")
        self.assertTrue(certification_set.contains_synthetic_completion)
        self.assertEqual(
            certification_set.case_ids.mapped("completion_provenance"), before
        )
        self.assertEqual(self._completion_diagnostics(certification_set), [])

    def test_other_synthetic_profiles_still_block_official_submission(self):
        certification_set = self._import_set()
        certification_set.action_generate_test_receiver_activities()
        note = certification_set.case_ids[1]
        provenance = deepcopy(note.completion_provenance)
        provenance["entries"].append(
            {
                "origin": "synthetic_local",
                "profile": "note_text_v1",
                "field": "sii_reference_reason",
            }
        )
        note._write_internal({"completion_provenance": provenance})
        self.assertTrue(self._completion_diagnostics(certification_set))

    def test_catalogue_profile_cannot_authorize_other_fields(self):
        certification_set = self._import_set()
        certification_set.action_generate_test_receiver_activities()
        case = certification_set.case_ids[0]
        provenance = deepcopy(case.completion_provenance)
        entry = dict(provenance["entries"][0], field="partner_id")
        provenance["entries"].append(entry)
        case._write_internal({"completion_provenance": provenance})
        self.assertTrue(self._completion_diagnostics(certification_set))

    def test_missing_or_changed_catalogue_provenance_blocks_submission(self):
        certification_set = self._import_set()
        certification_set.action_generate_test_receiver_activities()
        case = certification_set.case_ids[0]
        original = deepcopy(case.synthetic_receiver_activity)
        case._write_internal(
            {"synthetic_receiver_activity": dict(original, catalogue_digest="changed")}
        )
        self.assertTrue(self._completion_diagnostics(certification_set))
        case._write_internal(
            {"synthetic_receiver_activity": original, "completion_provenance": False}
        )
        self.assertTrue(self._completion_diagnostics(certification_set))

    def test_catalogue_description_must_match_its_code(self):
        certification_set = self._import_set()
        certification_set.action_generate_test_receiver_activities()
        case = certification_set.case_ids[0]
        activity = dict(
            case.synthetic_receiver_activity, name="Not a catalogue description"
        )
        case._write_internal({"synthetic_receiver_activity": activity})
        self.assertTrue(self._completion_diagnostics(certification_set))

    def test_note_previous_activity_must_match_the_referenced_invoice(self):
        certification_set = self._import_set()
        certification_set.action_generate_test_receiver_activities()
        invoice = certification_set.case_ids[0]
        selected = SiiActivityCatalogue().select(
            "different-test", invoice.synthetic_receiver_activity["code"]
        )
        activity = {
            **invoice.synthetic_receiver_activity,
            "code": selected["code"],
            "name": selected["name"],
        }
        invoice._store_synthetic_receiver_activity(
            {"activity": activity, "correction": None}
        )
        self.assertTrue(self._completion_diagnostics(certification_set))

    def test_catalogue_exception_does_not_apply_to_demo_or_production(self):
        certification_set = self._import_set()
        certification_set.action_generate_test_receiver_activities()
        for environment in ("demo", "production"):
            record = SimpleNamespace(
                contains_synthetic_completion=True,
                completion_mode="official_sii",
                sii_environment="certification",
                case_ids=certification_set.case_ids,
                company_id=SimpleNamespace(
                    dte_sii_environment=environment, dte_certification_enabled=True
                ),
            )
            with self.subTest(environment=environment):
                self.assertTrue(self._completion_diagnostics(record))

    def test_removing_the_synthetic_mark_does_not_bypass_preflight(self):
        certification_set = self._import_set()
        certification_set.action_generate_test_receiver_activities()
        certification_set._write_internal({"contains_synthetic_completion": False})
        self.assertTrue(self._completion_diagnostics(certification_set))

    def test_unmarked_synthetic_text_without_its_activity_is_still_detected(self):
        certification_set = self._import_set()
        certification_set.action_generate_test_receiver_activities()
        certification_set._write_internal({"contains_synthetic_completion": False})
        certification_set.case_ids._write_internal(
            {
                "synthetic_receiver_activity": False,
                "completion_provenance": False,
            }
        )
        self.assertTrue(self._completion_diagnostics(certification_set))

    def test_local_purpose_is_preserved_when_requested(self):
        certification_set = self._import_set()
        certification_set._populate_test_receiver_activities(
            target_mode="local_synthetic"
        )
        self.assertEqual(certification_set.completion_mode, "local_synthetic")
        self.assertTrue(self._completion_diagnostics(certification_set))

    def test_signed_xml_activity_and_correction_must_match_catalogue_selection(self):
        certification_set = self._import_set()
        certification_set.action_generate_test_receiver_activities()
        invoice, note, _debit = certification_set.case_ids.sorted("sequence")
        snapshot = self._capture_note_snapshot(note, invoice)
        snapshot["ted_xml"] = b'<TED version="1.0"/>'
        root = etree.fromstring(NoteXmlComposer().compose_unsigned_document(snapshot))
        ns = {"s": "http://www.sii.cl/SiiDte"}
        self.assertTrue(catalogue_activity_matches_xml(root, note))
        root.find(".//s:GiroRecep", namespaces=ns).text = "Changed activity"
        self.assertFalse(catalogue_activity_matches_xml(root, note))
        root.find(".//s:GiroRecep", namespaces=ns).text = snapshot["receiver"][
            "activity"
        ]
        root.find(".//s:DscItem", namespaces=ns).text = "Changed correction"
        self.assertFalse(catalogue_activity_matches_xml(root, note))
