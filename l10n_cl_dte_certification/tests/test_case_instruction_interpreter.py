# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from ..services.case_instruction_interpreter import (
    INTERPRETER_VERSION,
    interpret,
)


@tagged("post_install", "-at_install")
class TestCaseInstructionInterpreter(TransactionCase):
    def test_extracts_explicit_detail_fields_and_source_lines(self):
        result = interpret(
            "CASO 123-1\nDOCUMENTO FACTURA ELECTRONICA\n"
            "ITEM  CANTIDAD  PRECIO UNITARIO  UNIDAD MEDIDA\n"
            "Servicio A  2  1500  UN\nDESCUENTO GLOBAL 10%",
            "33",
            10,
        )

        self.assertEqual(result["interpreter_version"], INTERPRETER_VERSION)
        self.assertEqual(result["state"], "interpreted")
        self.assertEqual(
            result["items"],
            [
                {
                    "sequence": 1,
                    "source_line": 13,
                    "name": "Servicio A",
                    "quantity": "2",
                    "unit_price": "1500",
                    "uom": "UN",
                    "discount_percent": None,
                }
            ],
        )
        self.assertEqual(result["global_discount_percent"], "10")
        self.assertEqual(result["global_discount_source_line"], 14)

    def test_preserves_note_reference_reason_and_correction_lines(self):
        result = interpret(
            "CASO 123-2\nDOCUMENTO NOTA DE CREDITO ELECTRONICA\n"
            "REFERENCIA CASO 123-1\nRAZON REFERENCIA: CORRIGE TEXTO\n"
            "   DICE: Giro anterior\nDEBE DECIR: Giro corregido",
            "61",
            21,
        )

        self.assertEqual(result["state"], "interpreted")
        self.assertEqual(result["reference_case_identifier"], "123-1")
        self.assertEqual(result["reference_source_line"], 23)
        self.assertEqual(result["note_operation"], "text")
        self.assertEqual(result["reference_reason"], "CORRIGE TEXTO")
        self.assertEqual(result["reference_reason_source_line"], 24)
        self.assertEqual(
            result["text_correction"],
            [
                {"source_line": 25, "text": "   DICE: Giro anterior"},
                {"source_line": 26, "text": "DEBE DECIR: Giro corregido"},
            ],
        )

    def test_does_not_guess_missing_note_reference_or_discard_unknown_lines(self):
        result = interpret(
            "CASO 123-3\nDOCUMENTO NOTA DEBITO\nDato no reconocido", "56", 1
        )

        self.assertEqual(result["state"], "review_required")
        codes = {item["code"] for item in result["diagnostics"]}
        self.assertIn("note_reference_missing_or_unrecognized", codes)
        self.assertIn("note_reason_missing_or_unrecognized", codes)
        self.assertIn(
            {
                "code": "uninterpreted_line",
                "source_line": 3,
                "text": "Dato no reconocido",
            },
            result["diagnostics"],
        )

    def test_rejects_ambiguous_numeric_item_values_for_review(self):
        result = interpret(
            "CASO 123-4\nDOCUMENTO FACTURA ELECTRONICA\n"
            "ITEM  CANTIDAD  PRECIO UNITARIO\nServicio  1,5  100",
            "33",
            1,
        )

        self.assertEqual(result["items"], [])
        self.assertEqual(result["state"], "review_required")
        self.assertIn(
            "uninterpreted_line", {item["code"] for item in result["diagnostics"]}
        )

    def test_flags_out_of_range_item_decimals_without_losing_source_values(self):
        result = interpret(
            "CASO 123-5\nDOCUMENTO FACTURA ELECTRONICA\n"
            "ITEM  CANTIDAD  PRECIO UNITARIO  DESCUENTO ITEM\n"
            "Servicio A  0  100  101%",
            "33",
            1,
        )

        self.assertEqual(result["state"], "review_required")
        self.assertEqual(result["items"][0]["quantity"], "0")
        self.assertEqual(result["items"][0]["unit_price"], "100")
        self.assertEqual(result["items"][0]["discount_percent"], "101")
        self.assertEqual(
            result["diagnostics"],
            [
                {
                    "code": "item_value_out_of_range",
                    "source_line": 4,
                    "field": "quantity",
                    "value": "0",
                },
                {
                    "code": "item_value_out_of_range",
                    "source_line": 4,
                    "field": "discount_percent",
                    "value": "101",
                },
            ],
        )


@tagged("post_install", "-at_install")
class TestCaseInterpretationAction(TransactionCase):
    def test_manager_can_persist_versioned_interpretation(self):
        company = self.env.company
        manager = self.env["res.users"].create(
            {
                "name": "Synthetic Interpretation Manager",
                "login": "synthetic-interpretation-manager",
                "company_id": company.id,
                "company_ids": [Command.set([company.id])],
                "groups_id": [
                    Command.set(
                        [
                            self.env.ref("base.group_user").id,
                            self.env.ref("l10n_cl_dte_core.group_dte_manager").id,
                        ]
                    )
                ],
            }
        )
        source = (
            b"SET A - NUMERO DE ATENCION: 123\nCASO 123-1\n"
            b"DOCUMENTO FACTURA ELECTRONICA\nITEM  CANTIDAD  PRECIO UNITARIO\n"
            b"Servicio  1  100\nDESCUENTO GLOBAL 10%\n"
        )
        record = (
            self.env["l10n_cl.dte.certification.set"]
            .with_user(manager)
            ._create_from_import(company, "synthetic.txt", base64.b64encode(source))
        )
        record.action_parse_source()
        record.action_materialize_cases()

        with self.assertRaises(UserError):
            record.case_ids.sudo().write({"interpretation_state": "identified"})
        record.action_interpret_cases()

        self.assertEqual(record.case_ids.interpretation_state, "interpreted")
        self.assertEqual(record.case_ids.interpretation_version, INTERPRETER_VERSION)
        self.assertEqual(record.case_ids.global_discount_percent, "10")
        self.assertEqual(record.case_ids.global_discount_source_line, 6)
        self.assertEqual(
            record.case_ids.interpretation["items"][0]["unit_price"], "100"
        )
        self.assertEqual(record.case_coverage_state, "complete")
        self.assertEqual(record.case_interpreted_count, 1)
        self.assertEqual(record.case_pending_count, 0)
        self.assertEqual(len(record.case_ids.line_ids), 1)
        case_line = record.case_ids.line_ids
        self.assertEqual(case_line.item_name, "Servicio")
        self.assertEqual(case_line.quantity, "1")
        self.assertEqual(case_line.unit_price, "100")
        self.assertEqual(case_line.source_line, 5)
        self.assertEqual(case_line.source_digest, record.source_digest)
        with self.assertRaises(UserError):
            self.env["l10n_cl.dte.certification.case.line"].create(
                {"case_id": case_line.case_id.id}
            )
        with self.assertRaises(UserError):
            case_line.sudo().write({"unit_price": "999"})
        with self.assertRaises(UserError):
            case_line.sudo().unlink()
        record.action_interpret_cases()
        self.assertEqual(len(record.case_ids.line_ids), 1)
        self.assertEqual(
            record.action_check_case_coverage()["params"]["type"], "success"
        )

    def test_coverage_keeps_unsupported_case_visible_as_a_blocker(self):
        company = self.env.company
        manager = self.env["res.users"].create(
            {
                "name": "Synthetic Coverage Manager",
                "login": "synthetic-coverage-manager",
                "company_id": company.id,
                "company_ids": [Command.set([company.id])],
                "groups_id": [
                    Command.set(
                        [
                            self.env.ref("base.group_user").id,
                            self.env.ref("l10n_cl_dte_core.group_dte_manager").id,
                        ]
                    )
                ],
            }
        )
        source = (
            b"SET A - NUMERO DE ATENCION: 123\n"
            b"CASO 123-1\nDOCUMENTO TIPO DESCONOCIDO\n"
        )
        record = (
            self.env["l10n_cl.dte.certification.set"]
            .with_user(manager)
            ._create_from_import(company, "unknown.txt", base64.b64encode(source))
        )
        record.action_parse_source()
        record.action_materialize_cases()

        self.assertEqual(record.case_pending_count, 1)
        self.assertEqual(record.case_coverage_state, "pending")
        record.action_interpret_cases()

        self.assertEqual(record.case_unsupported_count, 1)
        self.assertEqual(record.case_review_required_count, 1)
        self.assertEqual(record.case_pending_count, 0)
        self.assertEqual(record.case_coverage_state, "unsupported")
        self.assertEqual(
            record.action_check_case_coverage()["params"]["type"], "warning"
        )

    def test_manager_can_review_persisted_note_facts_and_diagnostics(self):
        company = self.env.company
        manager = self.env["res.users"].create(
            {
                "name": "Synthetic Note Interpretation Manager",
                "login": "synthetic-note-interpretation-manager",
                "company_id": company.id,
                "company_ids": [Command.set([company.id])],
                "groups_id": [
                    Command.set(
                        [
                            self.env.ref("base.group_user").id,
                            self.env.ref("l10n_cl_dte_core.group_dte_manager").id,
                        ]
                    )
                ],
            }
        )
        source = (
            b"SET A - NUMERO DE ATENCION: 123\nCASO 123-2\n"
            b"DOCUMENTO NOTA DE CREDITO ELECTRONICA\n"
            b"REFERENCIA CASO 123-1\nRAZON REFERENCIA: CORRIGE TEXTO\n"
            b"DICE: Giro anterior\nDEBE DECIR: Giro corregido\n"
            b"Dato que requiere revision\n"
        )
        record = (
            self.env["l10n_cl.dte.certification.set"]
            .with_user(manager)
            ._create_from_import(
                company, "synthetic-note.txt", base64.b64encode(source)
            )
        )
        record.action_parse_source()
        record.action_materialize_cases()
        record.action_interpret_cases()

        case = record.case_ids
        self.assertEqual(case.interpretation_state, "review_required")
        self.assertEqual(case.reference_case_identifier, "123-1")
        self.assertEqual(case.reference_source_line, 4)
        self.assertEqual(case.reference_reason, "CORRIGE TEXTO")
        self.assertEqual(case.reference_reason_source_line, 5)
        self.assertEqual(case.note_operation, "text")
        self.assertEqual(
            case.text_correction,
            [
                {"source_line": 6, "text": "DICE: Giro anterior"},
                {"source_line": 7, "text": "DEBE DECIR: Giro corregido"},
            ],
        )
        self.assertEqual(
            case.diagnostics,
            [
                {
                    "code": "uninterpreted_line",
                    "source_line": 8,
                    "text": "Dato que requiere revision",
                }
            ],
        )
