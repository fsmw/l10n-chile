# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from base64 import b64encode

from odoo import tools
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestBiceStatementImport(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.parser = cls.env["account.statement.import.sheet.parser"]
        cls.statement_model = cls.env["account.bank.statement"]
        cls.import_model = cls.env["account.statement.import"]
        cls.suspense_account = cls.env["account.account"].create(
            {
                "code": "BICEFXX",
                "name": "BICE Import Test Suspense",
                "account_type": "asset_current",
            }
        )
        cls.journal = cls.env["account.journal"].create(
            {
                "name": "BICE Import Test",
                "type": "bank",
                "code": "BICEF",
                "company_id": cls.env.company.id,
                "suspense_account_id": cls.suspense_account.id,
            }
        )
        cls.mapping_model = cls.env["account.statement.import.sheet.mapping"]
        cls.bice_mapping = cls.mapping_model.create(
            {
                "name": "BICE synthetic test mapping",
                "float_thousands_sep": "none",
                "float_decimal_sep": "dot",
                "file_encoding": "windows-1252",
                "delimiter": "comma",
                "timestamp_format": "%d-%m",
                "timestamp_column": "Fecha",
                "amount_type": "distinct_credit_debit",
                "amount_debit_column": "Cargos",
                "amount_credit_column": "Abonos",
                "balance_column": "Saldo Contable",
                "description_column": "Descripción",
                "reference_column": "Documento",
                "header_lines_skip_count": 10,
                "footer_lines_skip_count": 7,
                "amount_inverse_sign": False,
            }
        )
        cls.bice_ttao_mapping = cls.mapping_model.create(
            {
                "name": "BICE TTAO layout test mapping",
                "float_thousands_sep": "none",
                "float_decimal_sep": "dot",
                "file_encoding": "windows-1252",
                "delimiter": "comma",
                "timestamp_format": "%Y%m%d",
                "timestamp_column": "FECHA",
                "amount_type": "distinct_credit_debit",
                "amount_debit_column": "CARGOS",
                "amount_credit_column": "ABONOS",
                "description_column": "DESCRIPCION",
                "reference_column": "DOCUMENTO",
                "header_lines_skip_count": 12,
                "footer_lines_skip_count": 8,
                "amount_inverse_sign": False,
            }
        )

    def _fixture_bytes(self, name):
        path = f"l10n_cl_bank_statement_import_bice/tests/fixtures/{name}"
        with tools.file_open(path, "rb") as fixture:
            return fixture.read()

    def _import_fixture(self, name):
        return self.import_model.with_context(journal_id=self.journal.id).create(
            {
                "statement_filename": name,
                "statement_file": b64encode(self._fixture_bytes(name)),
                "sheet_mapping_id": self.bice_mapping.id,
            }
        )

    def _statement(self):
        return self.statement_model.search(
            [("journal_id", "=", self.journal.id)], limit=1
        )

    def test_addon_installs_ready_to_use_ttao_mapping(self):
        mapping = self.env.ref(
            "l10n_cl_bank_statement_import_bice.bice_ttao_statement_mapping"
        )
        self.assertEqual(mapping.name, "BICE XLS (TTAO)")
        self.assertEqual(mapping.timestamp_format, "%Y%m%d")
        self.assertEqual(mapping.timestamp_column, "FECHA")
        self.assertEqual(mapping.amount_debit_column, "CARGOS")
        self.assertEqual(mapping.amount_credit_column, "ABONOS")
        self.assertFalse(mapping.balance_column)
        self.assertEqual(mapping.header_lines_skip_count, 12)
        self.assertEqual(mapping.footer_lines_skip_count, 8)

    def test_imports_original_layout_with_continuation_dates_and_bank_signs(self):
        filename = "bice_statement_synthetic.xls"
        file_data = self._fixture_bytes(filename)
        parsed = self.parser.with_context(journal_id=self.journal.id).parse(
            file_data, self.bice_mapping, filename
        )[2][0]
        self.assertEqual(parsed["balance_start"], 1000.0, repr(parsed))
        self.assertEqual(parsed["balance_end_real"], 1100.0)
        self.assertEqual(
            [transaction["date"].date() for transaction in parsed["transactions"]],
            [parsed["date"]] * 3,
        )
        self.assertEqual(
            [float(transaction["amount"]) for transaction in parsed["transactions"]],
            [-100.0, 200.0, 0.0],
        )

        wizard = self._import_fixture(filename)
        wizard.import_file_button()

        statement = self._statement()
        self.assertTrue(statement)
        self.assertEqual(len(statement.line_ids), 3)
        self.assertEqual(statement.balance_start, 1000.0)
        self.assertEqual(statement.balance_end_real, 1100.0)
        self.assertAlmostEqual(
            statement.line_ids.filtered(lambda line: line.ref == "D-001").amount,
            -100.0,
        )
        self.assertAlmostEqual(
            statement.line_ids.filtered(lambda line: line.ref == "D-002").amount,
            200.0,
        )
        self.assertEqual(
            statement.line_ids.filtered(lambda line: line.ref == "D-003").amount,
            0.0,
        )
        self.assertFalse(self.bice_mapping.amount_inverse_sign)

    def test_bice_import_keeps_bank_signs_when_inverse_flag_is_enabled(self):
        self.bice_mapping.amount_inverse_sign = True
        file_data = self._fixture_bytes("bice_statement_synthetic.xls")

        parsed = self.parser.with_context(journal_id=self.journal.id).parse(
            file_data, self.bice_mapping, "bice_statement_synthetic.xls"
        )[2][0]

        self.assertEqual(parsed["balance_start"], 1000.0)
        self.assertEqual(parsed["balance_end_real"], 1100.0)
        self.assertEqual(
            [float(transaction["amount"]) for transaction in parsed["transactions"]],
            [-100.0, 200.0, 0.0],
        )
        self.assertTrue(self.bice_mapping.amount_inverse_sign)

    def test_imports_ttao_cartola_layout_and_period_summary_balances(self):
        filename = "bice_ttao_layout_synthetic.xls"
        data = self._fixture_bytes(filename)
        parsed = self.parser.with_context(journal_id=self.journal.id).parse(
            data, self.bice_ttao_mapping, filename
        )[2][0]

        self.assertEqual(parsed["balance_start"], 1000.25)
        self.assertEqual(parsed["balance_end_real"], 1100.5)
        self.assertEqual(
            [float(line["amount"]) for line in parsed["transactions"]],
            [0.0, 0.0, -100.0, 200.25],
        )
        self.assertEqual(
            [line["ref"] for line in parsed["transactions"]],
            ["D-103", "D-104", "D-101", "D-102"],
        )

        wizard = self.import_model.with_context(journal_id=self.journal.id).create(
            {
                "statement_filename": filename,
                "statement_file": b64encode(data),
                "sheet_mapping_id": self.bice_ttao_mapping.id,
            }
        )
        wizard.import_file_button()
        statement = self._statement()
        self.assertEqual(statement.balance_start, 1000.25)
        self.assertEqual(statement.balance_end_real, 1100.5)
        self.assertEqual(len(statement.line_ids), 4)

    def test_invalid_continuation_date_creates_no_statement(self):
        wizard = self._import_fixture("bice_statement_invalid_date_synthetic.xls")
        with self.assertRaisesRegex(UserError, "before any valid transaction date"):
            wizard.import_file_button()
        self.assertFalse(self._statement())

    def test_non_bice_csv_keeps_saved_mapping_sign(self):
        mapping = self.mapping_model.create(
            {
                "name": "Generic statement test mapping",
                "float_thousands_sep": "none",
                "float_decimal_sep": "dot",
                "timestamp_format": "%d-%m",
                "timestamp_column": "Date",
                "amount_type": "simple_value",
                "amount_column": "Amount",
                "header_lines_skip_count": 1,
                "amount_inverse_sign": False,
            }
        )

        result = self.parser.with_context(journal_id=self.journal.id).parse(
            b"Date,Amount\n02-01,5.00\n", mapping, "generic.csv"
        )

        self.assertEqual(result[2][0]["transactions"][0]["amount"], "5.0")
        self.assertFalse(mapping.amount_inverse_sign)
