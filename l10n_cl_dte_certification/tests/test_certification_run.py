# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCertificationRunSnapshots(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.company
        cls.manager = cls.env["res.users"].create(
            {
                "name": "Synthetic Run Manager",
                "login": "synthetic-run-manager",
                "company_id": company.id,
                "company_ids": [Command.set([company.id])],
                "groups_id": [
                    Command.set(
                        [
                            cls.env.ref("base.group_user").id,
                            cls.env.ref("l10n_cl_dte_core.group_dte_manager").id,
                        ]
                    )
                ],
            }
        )
        source = (
            b"SET A - NUMERO DE ATENCION: 123\n"
            b"CASO 123-1\nDOCUMENTO FACTURA ELECTRONICA\n"
            b"ITEM  CANTIDAD  PRECIO UNITARIO  UNIDAD MEDIDA\n"
            b"Servicio sintetico  2  1250  UN\n"
            b"CASO 123-2\nDOCUMENTO NOTA DE CREDITO ELECTRONICA\n"
            b"REFERENCIA CASO 123-1\nRAZON REFERENCIA: CORRIGE TEXTO\n"
            b"DICE: Actividad anterior\nDEBE DECIR: Actividad corregida\n"
            b"CASO 123-3\nDOCUMENTO NOTA DE DEBITO ELECTRONICA\n"
            b"REFERENCIA CASO 123-2\nRAZON REFERENCIA: ANULA NOTA DE CREDITO\n"
        )
        cls.certification_set = (
            cls.env["l10n_cl.dte.certification.set"]
            .with_user(cls.manager)
            ._create_from_import(
                company, "synthetic-run-source.txt", base64.b64encode(source)
            )
        )
        cls.certification_set.action_parse_source()
        cls.certification_set.action_materialize_cases()
        cls.certification_set.action_interpret_cases()

    def test_creates_immutable_run_snapshot_with_reference_chain(self):
        cases = self.certification_set.case_ids.sorted("sequence")
        moves_before = self.env["account.move"].search_count([])
        run = (
            self.env["l10n_cl.dte.certification.run"]
            .with_user(self.manager)
            ._create_from_selection(self.certification_set, cases)
        )

        self.assertEqual(run.revision, 1)
        self.assertEqual(run.state, "draft")
        self.assertEqual(run.set_digest, self.certification_set.source_digest)
        self.assertEqual(len(run.snapshot_digest), 64)
        self.assertEqual(run.line_count, 3)
        invoice_line, note_line, cancellation_line = run.line_ids.sorted("sequence")
        self.assertEqual(invoice_line.case_id, cases[0])
        self.assertEqual(note_line.case_id, cases[1])
        self.assertEqual(note_line.referenced_line_id, invoice_line)
        self.assertEqual(cancellation_line.referenced_line_id, note_line)
        self.assertEqual(cancellation_line.document_code, "56")
        self.assertEqual(
            invoice_line.snapshot["details"][0],
            {
                "sequence": 1,
                "source_line": 5,
                "name": "Servicio sintetico",
                "quantity": "2",
                "unit_price": "1250",
                "discount_percent": False,
                "unit_of_measure": "UN",
                "source_digest": self.certification_set.source_digest,
                "interpretation_version": "sii_case_instructions_v3",
            },
        )
        self.assertEqual(
            note_line.snapshot["interpretation"]["text_correction"],
            [
                {"source_line": 10, "text": "DICE: Actividad anterior"},
                {"source_line": 11, "text": "DEBE DECIR: Actividad corregida"},
            ],
        )
        self.assertEqual(
            note_line.snapshot["text_correction_content"],
            [
                {
                    "kind": "dice",
                    "value": "Actividad anterior",
                    "source_line": 10,
                    "source_end_line": 10,
                },
                {
                    "kind": "debe_decir",
                    "value": "Actividad corregida",
                    "source_line": 11,
                    "source_end_line": 11,
                },
            ],
        )
        self.assertEqual(self.env["account.move"].search_count([]), moves_before)
        with self.assertRaises(UserError):
            run.sudo().write({"state": "blocked"})
        with self.assertRaises(UserError):
            note_line.sudo().unlink()

        second_run = (
            self.env["l10n_cl.dte.certification.run"]
            .with_user(self.manager)
            ._create_from_selection(self.certification_set, cases)
        )
        self.assertEqual(second_run.revision, 2)

    def test_rejects_note_selection_without_its_reference_before_creating_run(self):
        cases = self.certification_set.case_ids.sorted("sequence")
        run_model = self.env["l10n_cl.dte.certification.run"].with_user(self.manager)

        with self.assertRaisesRegex(ValidationError, "selection is incomplete"):
            run_model._create_from_selection(self.certification_set, cases[1:])

        self.assertFalse(run_model.search([("set_id", "=", self.certification_set.id)]))

    def test_manager_wizard_defaults_all_cases_and_opens_created_run(self):
        wizard = (
            self.env["l10n_cl.dte.certification.run.wizard"]
            .with_user(self.manager)
            .with_context(default_set_id=self.certification_set.id)
            .create(
                {
                    "set_id": self.certification_set.id,
                }
            )
        )
        action = wizard.action_create_execution()
        run = self.env["l10n_cl.dte.certification.run"].browse(action["res_id"])

        self.assertEqual(action["res_model"], "l10n_cl.dte.certification.run")
        self.assertEqual(len(run.line_ids), len(self.certification_set.case_ids))

    def test_rejects_direct_run_and_line_creation(self):
        with self.assertRaises(UserError):
            self.env["l10n_cl.dte.certification.run"].sudo().create(
                {
                    "name": "Synthetic bypass",
                    "set_id": self.certification_set.id,
                }
            )
        with self.assertRaises(UserError):
            self.env["l10n_cl.dte.certification.run.line"].sudo().create({"run_id": 1})

    def test_envelope_export_rejects_unprepared_cases(self):
        run = (
            self.env["l10n_cl.dte.certification.run"]
            .with_user(self.manager)
            ._create_from_selection(
                self.certification_set,
                self.certification_set.case_ids.sorted("sequence"),
            )
        )
        run.company_id.sudo().write({"dte_sii_environment": "certification"})
        run._record_generation_state("prepared")

        with self.assertRaisesRegex(ValidationError, "no longer matches"):
            run.action_export_envelope()

        self.assertFalse(run.envelope_xml)
        self.assertFalse(run.envelope_digest)

    def test_submit_envelope_creates_one_linked_gateway_record(self):
        run = (
            self.env["l10n_cl.dte.certification.run"]
            .with_user(self.manager)
            ._create_from_selection(
                self.certification_set,
                self.certification_set.case_ids.sorted("sequence"),
            )
        )
        run.company_id.sudo().write({"dte_sii_environment": "certification"})
        run._record_generation_state("prepared")
        envelope = (
            b'<EnvioDTE xmlns="http://www.sii.cl/SiiDte"><SetDTE>'
            b"<Caratula><RutEnvia>12345678-5</RutEnvia></Caratula>"
            b"</SetDTE></EnvioDTE>"
        )
        run._record_exported_envelope(envelope)
        gateway_factory = self.env["l10n_cl.sii.gateway.factory"]
        submission_model = self.env["l10n_cl.sii.envelope.submission"]

        with (
            patch.object(type(run.line_ids), "_validate_envelope_case_result"),
            patch(
                "odoo.addons.l10n_cl_dte_certification.models.certification_run.SiiEnvelopeComposer.validate"
            ),
            patch(
                "odoo.addons.l10n_cl_dte_certification.models.certification_run.SiiEnvelopeComposer.get_sender_vat",
                return_value="12345678-5",
            ),
            patch.object(type(gateway_factory), "_get_ready_envelope_gateway"),
            patch.object(
                type(submission_model),
                "action_enqueue_send",
                return_value={"type": "ir.actions.act_window"},
            ) as enqueue_send,
        ):
            first_action = run.action_send_certification_envelope()
            submission_id = run.sii_submission_id.id
            second_action = run.action_send_certification_envelope()

        self.assertTrue(submission_id)
        self.assertEqual(first_action, second_action)
        self.assertEqual(submission_model.search_count([]), 1)
        self.assertEqual(run.sii_submission_id.request_digest, run.envelope_digest)
        self.assertEqual(enqueue_send.call_count, 2)

    def test_reconcile_result_requires_an_imported_gateway_result(self):
        run = (
            self.env["l10n_cl.dte.certification.run"]
            .with_user(self.manager)
            ._create_from_selection(
                self.certification_set,
                self.certification_set.case_ids.sorted("sequence"),
            )
        )
        run.company_id.sudo().write({"dte_sii_environment": "certification"})
        run._record_generation_state("prepared")
        envelope = b"synthetic envelope evidence"
        run._record_exported_envelope(envelope)
        submission = (
            self.env["l10n_cl.sii.envelope.submission"]
            .with_user(self.manager)
            ._create_from_workflow(
                {
                    "name": "Synthetic certification result",
                    "company_id": run.company_id.id,
                    "environment": "certification",
                    "request_xml": base64.b64encode(envelope),
                    "request_filename": "synthetic.xml",
                    "request_digest": run.envelope_digest,
                    "idempotency_key": "synthetic-certification-result",
                }
            )
        )
        submission._write_internal({"state": "submitted", "track_id": "123456789"})
        run._record_sii_submission(submission)

        with self.assertRaisesRegex(UserError, "Import the SII result XML"):
            run.action_reconcile_sii_result()
