# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase

from ..services.case_generation_service import CaseGenerationService


@tagged("post_install", "-at_install")
class TestCertificationCaseGeneration(DteInvoiceTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        source = (
            b"SET TEST - NUMERO DE ATENCION: 321\n"
            b"CASO 321-1\nDOCUMENTO FACTURA ELECTRONICA\n"
            b"ITEM  CANTIDAD  PRECIO UNITARIO  UNIDAD MEDIDA\n"
            b"Servicio sintetico  2  1250  UN\n"
            b"CASO 321-2\nDOCUMENTO NOTA DE CREDITO ELECTRONICA\n"
            b"REFERENCIA CASO 321-1\nRAZON REFERENCIA: CORRIGE TEXTO\n"
            b"DICE: Actividad anterior\nDEBE DECIR: Actividad corregida\n"
        )
        cls.certification_set = cls.env[
            "l10n_cl.dte.certification.set"
        ]._create_from_import(
            cls.company, "synthetic-generation-source.txt", base64.b64encode(source)
        )
        cls.certification_set.action_parse_source()
        cls.certification_set.action_materialize_cases()
        cls.certification_set.action_interpret_cases()
        cls.execution = cls.env["l10n_cl.dte.certification.run"]._create_from_selection(
            cls.certification_set, cls.certification_set.case_ids
        )
        cls.journal = cls.env["account.journal"].search(
            [("company_id", "=", cls.company.id), ("type", "=", "sale")], limit=1
        )
        cls.journal.sudo().write({"l10n_latam_use_documents": True})
        cls.income_account = cls.env["account.account"].search(
            [
                ("company_ids", "in", [cls.company.id]),
                ("account_type", "=", "income"),
            ],
            limit=1,
        )

    def test_generates_linked_draft_without_posting_or_consuming_caf(self):
        folios_before = self.env["l10n_cl.caf.folio"].search_count([])
        created_lines = CaseGenerationService(self.env).generate(
            self.execution,
            journal=self.journal,
            vat_tax=self.tax_19,
            income_account=self.income_account,
            partner_by_line={
                self.execution.line_ids.filtered(
                    lambda item: item.document_code == "33"
                ).id: self.receiver
            },
            selected_lines=self.execution.line_ids.filtered(
                lambda item: item.document_code == "33"
            ),
        )
        line = created_lines.ensure_one()
        self.assertEqual(line.generation_state, "generated")
        self.assertEqual(self.execution.state, "partial")
        note_line = self.execution.line_ids.filtered(
            lambda item: item.document_code == "61"
        )
        self.assertEqual(note_line.generation_state, "pending")
        self.assertEqual(line.move_id.state, "draft")
        self.assertEqual(line.move_id.dte_certification_run_line_id, line)
        self.assertEqual(line.move_id.invoice_line_ids.quantity, 2)
        self.assertEqual(line.move_id.invoice_line_ids.price_unit, 1250)
        self.assertEqual(line.move_id.invoice_line_ids.tax_ids, self.tax_19)
        self.assertEqual(self.env["l10n_cl.caf.folio"].search_count([]), folios_before)
        with self.assertRaisesRegex(UserError, "cannot be posted"):
            line.move_id.action_post()

    def test_missing_partner_fails_before_any_draft_is_created(self):
        before = self.env["account.move"].search_count([])
        with self.assertRaisesRegex(ValidationError, "Choose a customer"):
            CaseGenerationService(self.env).generate(
                self.execution,
                journal=self.journal,
                vat_tax=self.tax_19,
                income_account=self.income_account,
                partner_by_line={},
            )
        self.assertEqual(self.env["account.move"].search_count([]), before)
        self.assertEqual(self.execution.state, "draft")

    def test_generates_zero_value_text_correction_draft_with_invoice_reference(self):
        invoice_line = self.execution.line_ids.filtered(
            lambda item: item.document_code == "33"
        )
        note_line = self.execution.line_ids.filtered(
            lambda item: item.document_code == "61"
        )
        folios_before = self.env["l10n_cl.caf.folio"].search_count([])

        created_lines = CaseGenerationService(self.env).generate(
            self.execution,
            journal=self.journal,
            vat_tax=self.tax_19,
            income_account=self.income_account,
            partner_by_line={invoice_line.id: self.receiver},
            selected_lines=invoice_line | note_line,
        )

        self.assertEqual(set(created_lines.ids), {invoice_line.id, note_line.id})
        self.assertEqual(self.execution.state, "generated")
        invoice = invoice_line.move_id
        note = note_line.move_id
        self.assertEqual(invoice.state, "draft")
        self.assertEqual(note.state, "draft")
        self.assertEqual(note.move_type, "out_refund")
        self.assertEqual(note.l10n_latam_document_type_id.code, "61")
        self.assertEqual(note.reversed_entry_id, invoice)
        self.assertEqual(note.partner_id, invoice.partner_id)
        self.assertEqual(note.amount_total, 0)
        self.assertFalse(note.invoice_line_ids.tax_ids)
        self.assertEqual(note.dte_certification_run_line_id, note_line)
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
        self.assertEqual(self.env["l10n_cl.caf.folio"].search_count([]), folios_before)
        with self.assertRaisesRegex(UserError, "cannot be posted"):
            note.action_post()

    def test_text_correction_requires_selected_or_generated_target_invoice(self):
        note_line = self.execution.line_ids.filtered(
            lambda item: item.document_code == "61"
        )
        before = self.env["account.move"].search_count([])

        with self.assertRaisesRegex(ValidationError, "Select the referenced invoice"):
            CaseGenerationService(self.env).generate(
                self.execution,
                journal=self.journal,
                vat_tax=self.tax_19,
                income_account=self.income_account,
                selected_lines=note_line,
            )

        self.assertEqual(self.env["account.move"].search_count([]), before)
        self.assertEqual(self.execution.state, "draft")
