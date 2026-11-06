# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCertificationCaseSnapshots(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.manager = cls.env["res.users"].create(
            {
                "name": "Synthetic Certification Manager",
                "login": "synthetic-cert-manager",
                "company_id": cls.company.id,
                "company_ids": [Command.set([cls.company.id])],
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

    def _create_set(self, content):
        return (
            self.env["l10n_cl.dte.certification.set"]
            .with_user(self.manager)
            ._create_from_import(
                self.company, "synthetic-set.txt", base64.b64encode(content)
            )
        )

    def test_materializes_ordered_immutable_snapshots_from_full_source(self):
        source = (
            "SET A - NUMERO DE ATENCION: 123\n"
            "CASO 123-2\nDOCUMENTO FACTURA ELECTRONICA\nTexto exacto Ñ\n"
            "CASO 123-1\nDOCUMENTO NOTA DE CREDITO ELECTRONICA\nReferencia\n"
        ).encode()
        record = self._create_set(source)

        record.action_parse_source()
        record.action_materialize_cases()

        cases = record.case_ids.sorted("sequence")
        self.assertEqual(record.case_count, 2)
        self.assertEqual(cases.mapped("case_identifier"), ["123-2", "123-1"])
        self.assertEqual(cases.mapped("sequence"), [1, 2])
        self.assertEqual(cases.mapped("document_code"), ["33", "61"])
        self.assertEqual(
            cases[0].instruction_text,
            "CASO 123-2\nDOCUMENTO FACTURA ELECTRONICA\nTexto exacto Ñ",
        )
        self.assertEqual(cases[0].source_digest, record.source_digest)
        self.assertEqual(base64.b64decode(record.source_file), source)
        with self.assertRaises(UserError):
            record.action_materialize_cases()

    def test_preserves_unsupported_cases_as_visible_blockers(self):
        record = self._create_set(
            b"SET A - NUMERO DE ATENCION: 123\n" b"CASO 123-1\nDOCUMENTO DESCONOCIDO\n"
        )

        record.action_parse_source()
        record.action_materialize_cases()

        case = record.case_ids
        self.assertEqual(case.interpretation_state, "unsupported")
        self.assertEqual(case.diagnostics[0]["code"], "unsupported_document_type")
        self.assertEqual(case.instruction_text, "CASO 123-1\nDOCUMENTO DESCONOCIDO")

    def test_cannot_materialize_without_a_valid_parsed_snapshot(self):
        record = self._create_set(b"synthetic invalid structure")
        record.action_parse_source()

        with self.assertRaises(UserError):
            record.action_materialize_cases()

    def test_case_records_cannot_be_created_directly(self):
        with self.assertRaises(UserError):
            self.env["l10n_cl.dte.certification.case"].create(
                {
                    "set_id": 1,
                    "company_id": self.company.id,
                    "attention_number": "123",
                    "sequence": 1,
                    "section_sequence": 1,
                    "case_identifier": "123-1",
                    "document_code": "33",
                    "source_start_line": 1,
                    "source_end_line": 2,
                    "instruction_text": "synthetic",
                    "source_digest": "synthetic",
                    "interpretation_state": "identified",
                }
            )
