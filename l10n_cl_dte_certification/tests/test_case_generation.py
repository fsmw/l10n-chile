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
        with self.assertRaisesRegex(UserError, "immutable"):
            line.move_id.write({"invoice_date": "2026-01-02"})

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

    def test_generates_zero_value_debit_note_for_text_correction_cancellation(self):
        source = (
            b"SET TEST - NUMERO DE ATENCION: 654\n"
            b"CASO 654-1\nDOCUMENTO FACTURA ELECTRONICA\n"
            b"ITEM  CANTIDAD  PRECIO UNITARIO  UNIDAD MEDIDA\n"
            b"Synthetic service  2  1250  UN\n"
            b"CASO 654-2\nDOCUMENTO NOTA DE CREDITO ELECTRONICA\n"
            b"REFERENCIA CASO 654-1\nRAZON REFERENCIA: CORRIGE TEXTO\n"
            b"DICE: Old activity\nDEBE DECIR: Corrected activity\n"
            b"CASO 654-3\nDOCUMENTO NOTA DE DEBITO ELECTRONICA\n"
            b"REFERENCIA CASO 654-2\n"
            b"RAZON REFERENCIA: ANULA NOTA DE CREDITO\n"
        )
        certification_set = self.env[
            "l10n_cl.dte.certification.set"
        ]._create_from_import(
            self.company,
            "synthetic-text-correction-cancellation.txt",
            base64.b64encode(source),
        )
        certification_set.action_parse_source()
        certification_set.action_materialize_cases()
        certification_set.action_interpret_cases()
        execution = self.env["l10n_cl.dte.certification.run"]._create_from_selection(
            certification_set, certification_set.case_ids
        )
        invoice_line = execution.line_ids.filtered(
            lambda item: item.document_code == "33"
        )
        correction_line = execution.line_ids.filtered(
            lambda item: item.document_code == "61"
        )
        cancellation_line = execution.line_ids.filtered(
            lambda item: item.document_code == "56"
        )
        folios_before = self.env["l10n_cl.caf.folio"].search_count([])
        moves_before = self.env["account.move"].search_count([])

        with self.assertRaisesRegex(
            ValidationError, "Select the referenced type-61 correction"
        ):
            CaseGenerationService(self.env).generate(
                execution,
                journal=self.journal,
                income_account=self.income_account,
                selected_lines=cancellation_line,
            )
        self.assertEqual(self.env["account.move"].search_count([]), moves_before)
        self.assertEqual(execution.state, "draft")

        created_lines = CaseGenerationService(self.env).generate(
            execution,
            journal=self.journal,
            vat_tax=self.tax_19,
            income_account=self.income_account,
            partner_by_line={invoice_line.id: self.receiver},
            selected_lines=invoice_line | correction_line | cancellation_line,
        )

        self.assertEqual(
            set(created_lines.ids),
            {invoice_line.id, correction_line.id, cancellation_line.id},
        )
        invoice = invoice_line.move_id
        correction = correction_line.move_id
        cancellation = cancellation_line.move_id
        self.assertEqual(execution.state, "generated")
        self.assertEqual(correction.move_type, "out_refund")
        self.assertEqual(correction.l10n_latam_document_type_id.code, "61")
        self.assertEqual(correction.reversed_entry_id, invoice)
        self.assertEqual(correction.amount_total, 0)
        self.assertEqual(cancellation.move_type, "out_invoice")
        self.assertEqual(cancellation.l10n_latam_document_type_id.code, "56")
        self.assertEqual(cancellation.debit_origin_id, correction)
        self.assertEqual(cancellation.partner_id, correction.partner_id)
        self.assertEqual(cancellation.amount_total, 0)
        self.assertFalse(cancellation.invoice_line_ids.tax_ids)
        self.assertEqual(cancellation.state, "draft")
        self.assertEqual(cancellation.dte_certification_run_line_id, cancellation_line)
        self.assertEqual(self.env["l10n_cl.caf.folio"].search_count([]), folios_before)
        with self.assertRaisesRegex(UserError, "cannot be posted"):
            cancellation.action_post()

    def _return_execution(self, returned_quantities, duplicate_invoice_item=False):
        if isinstance(returned_quantities, str):
            returned_quantities = [returned_quantities]
        source = (
            b"SET TEST - NUMERO DE ATENCION: 777\n"
            b"CASO 777-1\nDOCUMENTO FACTURA ELECTRONICA\n"
            b"ITEM  CANTIDAD  PRECIO UNITARIO  UNIDAD MEDIDA  DESCUENTO ITEM\n"
            b"Servicio de prueba  2  1250  UN  10%\n"
        )
        if duplicate_invoice_item:
            source += b"Servicio de prueba  1  500  UN  0%\n"
        for sequence, quantity in enumerate(returned_quantities, start=2):
            source += (
                f"CASO 777-{sequence}\nDOCUMENTO NOTA DE CREDITO ELECTRONICA\n"
                "REFERENCIA CASO 777-1\n"
                "RAZON REFERENCIA: DEVOLUCION DE MERCADERIAS\n"
                "ITEM  CANTIDAD  UNIDAD MEDIDA\n"
                f"Servicio de prueba  {quantity}  UN\n"
            ).encode()
        certification_set = self.env[
            "l10n_cl.dte.certification.set"
        ]._create_from_import(
            self.company, "synthetic-return-source.txt", base64.b64encode(source)
        )
        certification_set.action_parse_source()
        certification_set.action_materialize_cases()
        certification_set.action_interpret_cases()
        execution = self.env["l10n_cl.dte.certification.run"]._create_from_selection(
            certification_set, certification_set.case_ids
        )
        return execution, execution.line_ids.sorted("sequence")

    def test_generates_partial_return_from_immutable_invoice_snapshot(self):
        execution, lines = self._return_execution("1")
        invoice_line, return_line = lines
        folios_before = self.env["l10n_cl.caf.folio"].search_count([])

        created = CaseGenerationService(self.env).generate(
            execution,
            journal=self.journal,
            vat_tax=self.tax_19,
            income_account=self.income_account,
            partner_by_line={invoice_line.id: self.receiver},
            selected_lines=lines,
        )

        self.assertEqual(set(created.ids), set(lines.ids))
        invoice = invoice_line.move_id
        note = return_line.move_id
        self.assertEqual(note.move_type, "out_refund")
        self.assertEqual(note.l10n_latam_document_type_id.code, "61")
        self.assertEqual(note.reversed_entry_id, invoice)
        self.assertEqual(note.state, "draft")
        self.assertEqual(note.partner_id, invoice.partner_id)
        self.assertEqual(note.invoice_line_ids.quantity, 1)
        self.assertEqual(note.invoice_line_ids.price_unit, 1250)
        self.assertEqual(note.invoice_line_ids.discount, 10)
        self.assertEqual(note.invoice_line_ids.tax_ids, self.tax_19)
        self.assertEqual(note.amount_total, 1339)
        self.assertEqual(execution.state, "generated")
        self.assertEqual(self.env["l10n_cl.caf.folio"].search_count([]), folios_before)
        with self.assertRaisesRegex(UserError, "cannot be posted"):
            note.action_post()

    def test_return_over_original_quantity_fails_before_creating_any_draft(self):
        execution, lines = self._return_execution("3")
        invoice_line = lines[0]
        before = self.env["account.move"].search_count([])

        with self.assertRaisesRegex(ValidationError, "exceeds the remaining"):
            CaseGenerationService(self.env).generate(
                execution,
                journal=self.journal,
                vat_tax=self.tax_19,
                income_account=self.income_account,
                partner_by_line={invoice_line.id: self.receiver},
                selected_lines=lines,
            )

        self.assertEqual(self.env["account.move"].search_count([]), before)
        self.assertEqual(execution.state, "draft")

    def test_later_return_cannot_exceed_balance_after_prior_generated_return(self):
        execution, lines = self._return_execution(["1", "2"])
        invoice_line, first_return, second_return = lines
        generator = CaseGenerationService(self.env)
        generator.generate(
            execution,
            journal=self.journal,
            vat_tax=self.tax_19,
            income_account=self.income_account,
            partner_by_line={invoice_line.id: self.receiver},
            selected_lines=invoice_line | first_return,
        )
        before = self.env["account.move"].search_count([])

        with self.assertRaisesRegex(ValidationError, "exceeds the remaining"):
            generator.generate(
                execution,
                journal=self.journal,
                vat_tax=self.tax_19,
                income_account=self.income_account,
                selected_lines=second_return,
            )

        self.assertEqual(self.env["account.move"].search_count([]), before)
        self.assertEqual(second_return.generation_state, "pending")

    def test_return_with_ambiguous_source_item_fails_before_draft_creation(self):
        execution, lines = self._return_execution("1", duplicate_invoice_item=True)
        invoice_line = lines[0]
        before = self.env["account.move"].search_count([])

        with self.assertRaisesRegex(ValidationError, "exactly one invoice line"):
            CaseGenerationService(self.env).generate(
                execution,
                journal=self.journal,
                vat_tax=self.tax_19,
                income_account=self.income_account,
                partner_by_line={invoice_line.id: self.receiver},
                selected_lines=lines,
            )

        self.assertEqual(self.env["account.move"].search_count([]), before)
        self.assertEqual(execution.state, "draft")

    def _invoice_cancellation_execution(self, include_return=False):
        source = (
            b"SET TEST - NUMERO DE ATENCION: 888\n"
            b"CASO 888-1\nDOCUMENTO FACTURA ELECTRONICA\n"
            b"ITEM  CANTIDAD  PRECIO UNITARIO  UNIDAD MEDIDA  DESCUENTO ITEM\n"
            b"Servicio de prueba  2  1250  UN  10%\n"
        )
        if include_return:
            source += (
                b"CASO 888-2\nDOCUMENTO NOTA DE CREDITO ELECTRONICA\n"
                b"REFERENCIA CASO 888-1\n"
                b"RAZON REFERENCIA: DEVOLUCION DE MERCADERIAS\n"
                b"ITEM  CANTIDAD  UNIDAD MEDIDA\n"
                b"Servicio de prueba  1  UN\n"
            )
        source += (
            b"CASO 888-3\nDOCUMENTO NOTA DE CREDITO ELECTRONICA\n"
            b"REFERENCIA CASO 888-1\nRAZON REFERENCIA: ANULA FACTURA\n"
        )
        certification_set = self.env[
            "l10n_cl.dte.certification.set"
        ]._create_from_import(
            self.company,
            "synthetic-invoice-cancellation-source.txt",
            base64.b64encode(source),
        )
        certification_set.action_parse_source()
        certification_set.action_materialize_cases()
        certification_set.action_interpret_cases()
        execution = self.env["l10n_cl.dte.certification.run"]._create_from_selection(
            certification_set, certification_set.case_ids
        )
        return execution, execution.line_ids.sorted("sequence")

    def test_generates_full_invoice_cancellation_from_snapshot(self):
        execution, lines = self._invoice_cancellation_execution()
        invoice_line, cancellation_line = lines
        folios_before = self.env["l10n_cl.caf.folio"].search_count([])

        created = CaseGenerationService(self.env).generate(
            execution,
            journal=self.journal,
            vat_tax=self.tax_19,
            income_account=self.income_account,
            partner_by_line={invoice_line.id: self.receiver},
            selected_lines=lines,
        )

        self.assertEqual(set(created.ids), set(lines.ids))
        invoice = invoice_line.move_id
        cancellation = cancellation_line.move_id
        self.assertEqual(cancellation.move_type, "out_refund")
        self.assertEqual(cancellation.l10n_latam_document_type_id.code, "61")
        self.assertEqual(cancellation.reversed_entry_id, invoice)
        self.assertEqual(cancellation.partner_id, invoice.partner_id)
        self.assertEqual(cancellation.state, "draft")
        self.assertEqual(cancellation.invoice_line_ids.quantity, 2)
        self.assertEqual(cancellation.invoice_line_ids.price_unit, 1250)
        self.assertEqual(cancellation.invoice_line_ids.discount, 10)
        self.assertEqual(cancellation.invoice_line_ids.tax_ids, self.tax_19)
        self.assertEqual(cancellation.amount_total, invoice.amount_total)
        self.assertEqual(self.env["l10n_cl.caf.folio"].search_count([]), folios_before)
        with self.assertRaisesRegex(UserError, "cannot be posted"):
            cancellation.action_post()

    def test_invoice_cancellation_blocks_after_generated_return(self):
        execution, lines = self._invoice_cancellation_execution(include_return=True)
        invoice_line, return_line, cancellation_line = lines
        generator = CaseGenerationService(self.env)
        generator.generate(
            execution,
            journal=self.journal,
            vat_tax=self.tax_19,
            income_account=self.income_account,
            partner_by_line={invoice_line.id: self.receiver},
            selected_lines=invoice_line | return_line,
        )
        before = self.env["account.move"].search_count([])

        with self.assertRaisesRegex(ValidationError, "another monetary adjustment"):
            generator.generate(
                execution,
                journal=self.journal,
                vat_tax=self.tax_19,
                income_account=self.income_account,
                selected_lines=cancellation_line,
            )

        self.assertEqual(self.env["account.move"].search_count([]), before)
        self.assertEqual(cancellation_line.generation_state, "pending")

    def _monetary_note_cancellation_execution(self, operation):
        source = (
            b"SET TEST - NUMERO DE ATENCION: 999\n"
            b"CASO 999-1\nDOCUMENTO FACTURA ELECTRONICA\n"
            b"ITEM  CANTIDAD  PRECIO UNITARIO  UNIDAD MEDIDA  DESCUENTO ITEM\n"
            b"Servicio de prueba  2  1250  UN  10%\n"
        )
        if operation == "return":
            source += (
                b"CASO 999-2\nDOCUMENTO NOTA DE CREDITO ELECTRONICA\n"
                b"REFERENCIA CASO 999-1\n"
                b"RAZON REFERENCIA: DEVOLUCION DE MERCADERIAS\n"
                b"ITEM  CANTIDAD  UNIDAD MEDIDA\n"
                b"Servicio de prueba  1  UN\n"
            )
        else:
            source += (
                b"CASO 999-2\nDOCUMENTO NOTA DE CREDITO ELECTRONICA\n"
                b"REFERENCIA CASO 999-1\nRAZON REFERENCIA: ANULA FACTURA\n"
            )
        source += (
            b"CASO 999-3\nDOCUMENTO NOTA DE DEBITO ELECTRONICA\n"
            b"REFERENCIA CASO 999-2\n"
            b"RAZON REFERENCIA: ANULA NOTA DE CREDITO\n"
        )
        certification_set = self.env[
            "l10n_cl.dte.certification.set"
        ]._create_from_import(
            self.company,
            "synthetic-monetary-note-cancellation.txt",
            base64.b64encode(source),
        )
        certification_set.action_parse_source()
        certification_set.action_materialize_cases()
        certification_set.action_interpret_cases()
        execution = self.env["l10n_cl.dte.certification.run"]._create_from_selection(
            certification_set, certification_set.case_ids
        )
        return execution, execution.line_ids.sorted("sequence")

    def test_generates_debit_cancellation_for_monetary_return_note(self):
        execution, lines = self._monetary_note_cancellation_execution("return")
        invoice_line, return_line, cancellation_line = lines
        folios_before = self.env["l10n_cl.caf.folio"].search_count([])

        created = CaseGenerationService(self.env).generate(
            execution,
            journal=self.journal,
            vat_tax=self.tax_19,
            income_account=self.income_account,
            partner_by_line={invoice_line.id: self.receiver},
            selected_lines=lines,
        )

        note = return_line.move_id
        cancellation = cancellation_line.move_id
        self.assertEqual(set(created.ids), set(lines.ids))
        self.assertEqual(cancellation.move_type, "out_invoice")
        self.assertEqual(cancellation.l10n_latam_document_type_id.code, "56")
        self.assertEqual(cancellation.debit_origin_id, note)
        self.assertEqual(cancellation.state, "draft")
        self.assertEqual(cancellation.amount_total, note.amount_total)
        self.assertEqual(cancellation.invoice_line_ids.quantity, 1)
        self.assertEqual(cancellation.invoice_line_ids.price_unit, 1250)
        self.assertEqual(cancellation.invoice_line_ids.discount, 10)
        self.assertEqual(cancellation.invoice_line_ids.tax_ids, self.tax_19)
        self.assertEqual(self.env["l10n_cl.caf.folio"].search_count([]), folios_before)
        with self.assertRaisesRegex(UserError, "cannot be posted"):
            cancellation.action_post()

    def test_generates_debit_cancellation_for_full_invoice_cancellation_note(self):
        execution, lines = self._monetary_note_cancellation_execution("cancel")
        invoice_line, note_line, cancellation_line = lines

        CaseGenerationService(self.env).generate(
            execution,
            journal=self.journal,
            vat_tax=self.tax_19,
            income_account=self.income_account,
            partner_by_line={invoice_line.id: self.receiver},
            selected_lines=lines,
        )

        note = note_line.move_id
        cancellation = cancellation_line.move_id
        self.assertEqual(note.amount_total, invoice_line.move_id.amount_total)
        self.assertEqual(cancellation.debit_origin_id, note)
        self.assertEqual(cancellation.l10n_latam_document_type_id.code, "56")
        self.assertEqual(cancellation.amount_total, note.amount_total)
        self.assertEqual(cancellation.invoice_line_ids.quantity, 2)
        self.assertEqual(cancellation.invoice_line_ids.price_unit, 1250)
        self.assertEqual(cancellation.invoice_line_ids.discount, 10)
