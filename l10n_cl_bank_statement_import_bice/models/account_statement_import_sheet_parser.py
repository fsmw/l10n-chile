# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import csv
import unicodedata
from datetime import date, datetime
from io import StringIO

import xlrd
from xlrd.xldate import xldate_as_datetime

from odoo import api, models
from odoo.exceptions import UserError

_BICE_HEADERS = {
    "fecha",
    "documento",
    "descripcion",
    "cargos",
    "abonos",
}
_BICE_LEGACY_HEADERS = _BICE_HEADERS | {"saldo contable", "linea sobregiro"}


class AccountStatementImportSheetParser(models.TransientModel):
    _inherit = "account.statement.import.sheet.parser"

    @api.model
    def parse(self, data_file, mapping, filename):
        normalized = self._normalize_bice_xls(data_file, mapping)
        if normalized is None:
            return super().parse(data_file, mapping, filename)
        normalized_file, balances = normalized

        parse_mapping = mapping
        if mapping.amount_inverse_sign:
            values = mapping.copy_data()[0]
            values["amount_inverse_sign"] = False
            parse_mapping = mapping.new(values)
        result = super().parse(normalized_file, parse_mapping, filename)
        if balances:
            for statement_data in result[2]:
                statement_data.update(
                    {
                        "balance_start": balances[0],
                        "balance_end_real": balances[1],
                    }
                )
        return result

    @api.model
    def _normalize_bice_xls(self, data_file, mapping):
        """Return an in-memory CSV representation for a matching BICE XLS."""
        try:
            workbook = xlrd.open_workbook(
                file_contents=data_file,
                encoding_override=mapping.file_encoding or None,
            )
        except xlrd.XLRDError:
            return None

        sheet = workbook.sheet_by_index(0)
        header_index = self._find_bice_header(sheet)
        if header_index is None:
            return None
        header = [
            self._normalize_label(value) for value in sheet.row_values(header_index)
        ]
        has_row_balances = _BICE_LEGACY_HEADERS.issubset(set(header))
        if not self._mapping_matches_bice(mapping, has_row_balances):
            return None
        if mapping.header_lines_skip_count != header_index + 1:
            return None

        first_data_row = mapping.header_lines_skip_count
        end_data_row = sheet.nrows - mapping.footer_lines_skip_count
        if end_data_row <= first_data_row:
            raise UserError(self.env._("The BICE statement has no transaction rows."))

        column_indexes = {name: header.index(name) for name in _BICE_HEADERS}
        date_index = column_indexes["fecha"]
        description_index = column_indexes["descripcion"]
        debit_index = column_indexes["cargos"]
        credit_index = column_indexes["abonos"]
        amount_indexes = self._mapped_amount_indexes(mapping, header)

        rows = []
        balances = None if has_row_balances else self._summary_balances(sheet, header)
        previous_date = None
        for row_index in range(sheet.nrows):
            values = self._read_row(sheet, row_index, workbook.datemode)
            if first_data_row <= row_index < end_data_row and any(
                str(value).strip() for value in values
            ):
                timestamp = values[date_index]
                timestamp_text = str(timestamp).strip()
                if timestamp_text == "-":
                    if previous_date is None:
                        raise UserError(
                            self.env._(
                                "BICE row %(row)s uses '-' for Fecha before any "
                                "valid transaction date.",
                                row=row_index + 1,
                            )
                        )
                    values[date_index] = previous_date.strftime(
                        mapping.timestamp_format
                    )
                elif timestamp_text:
                    previous_date = self._parse_bice_date(
                        timestamp, mapping.timestamp_format, row_index + 1
                    )
                    if isinstance(timestamp, datetime | date):
                        values[date_index] = previous_date.strftime(
                            mapping.timestamp_format
                        )
                else:
                    raise UserError(
                        self.env._(
                            "BICE row %(row)s has no value in Fecha.",
                            row=row_index + 1,
                        )
                    )

                if self._is_opening_balance(
                    values, description_index, amount_indexes, mapping
                ):
                    continue
                values[debit_index], values[credit_index] = (
                    values[credit_index],
                    values[debit_index],
                )
            rows.append(values)

        output = StringIO(newline="")
        writer = csv.writer(
            output,
            delimiter=mapping._get_column_delimiter_character() or ",",
            quotechar=mapping.quotechar or '"',
            lineterminator="\n",
        )
        writer.writerows(rows)
        encoding = mapping.file_encoding or "utf-8"
        try:
            return output.getvalue().encode(encoding), balances
        except UnicodeEncodeError as error:
            raise UserError(
                self.env._(
                    "The BICE statement contains characters that cannot be "
                    "represented with the selected encoding (%(encoding)s).",
                    encoding=encoding,
                )
            ) from error

    @api.model
    def _find_bice_header(self, sheet):
        for row_index in range(min(sheet.nrows, 40)):
            row = {
                self._normalize_label(value) for value in sheet.row_values(row_index)
            }
            if _BICE_HEADERS.issubset(row):
                return row_index
        return None

    @api.model
    def _mapping_matches_bice(self, mapping, has_row_balances):
        expected = {
            "timestamp_column": "fecha",
            "reference_column": "documento",
            "description_column": "descripcion",
            "amount_debit_column": "cargos",
            "amount_credit_column": "abonos",
        }
        if has_row_balances:
            expected["balance_column"] = "saldo contable"
        return (
            not mapping.no_header
            and not mapping.offset_column
            and mapping.amount_type == "distinct_credit_debit"
            and all(
                self._normalize_label(mapping[field_name]) == expected_label
                for field_name, expected_label in expected.items()
            )
            and (
                bool(mapping.balance_column) == has_row_balances
                and (
                    not has_row_balances
                    or self._normalize_label(mapping.balance_column) == "saldo contable"
                )
            )
        )

    @api.model
    def _summary_balances(self, sheet, _header):
        """Read the opening and closing balances in the BICE period summary."""
        for row_index in range(sheet.nrows - 1):
            labels = [
                self._normalize_label(value) for value in sheet.row_values(row_index)
            ]
            if "saldo inicial" not in labels or "saldo final" not in labels:
                continue
            opening_index = labels.index("saldo inicial")
            closing_index = labels.index("saldo final")
            values = sheet.row_values(row_index + 1)
            try:
                return float(values[opening_index]), float(values[closing_index])
            except (IndexError, TypeError, ValueError):
                raise UserError(
                    self.env._(
                        "The BICE period summary has invalid opening/closing balances."
                    )
                ) from None
        raise UserError(
            self.env._(
                "The BICE statement does not contain opening and closing balances."
            )
        )

    @api.model
    def _mapped_amount_indexes(self, mapping, header):
        indexes = []
        for field_name in (
            "amount_column",
            "amount_debit_column",
            "amount_credit_column",
        ):
            column_name = mapping[field_name]
            if column_name:
                index = header.index(self._normalize_label(column_name))
                if index not in indexes:
                    indexes.append(index)
        return indexes

    @api.model
    def _is_opening_balance(self, values, description_index, amount_indexes, mapping):
        description = self._normalize_label(values[description_index])
        if description != "saldo inicial":
            return False
        for index in amount_indexes:
            try:
                if self._parse_decimal(values[index], mapping):
                    return False
            except (TypeError, ValueError):
                return False
        return True

    @api.model
    def _parse_bice_date(self, value, timestamp_format, row_number):
        if isinstance(value, datetime):
            formatted = value.strftime(timestamp_format)
        elif isinstance(value, date):
            formatted = value.strftime(timestamp_format)
        else:
            formatted = str(value).strip()
        try:
            return datetime.strptime(formatted, timestamp_format)
        except ValueError as error:
            raise UserError(
                self.env._(
                    "BICE row %(row)s has an invalid Fecha value: %(value)s.",
                    row=row_number,
                    value=formatted,
                )
            ) from error

    @api.model
    def _read_row(self, sheet, row_index, datemode):
        values = []
        for column_index in range(sheet.row_len(row_index)):
            cell_type = sheet.cell_type(row_index, column_index)
            value = sheet.cell_value(row_index, column_index)
            if cell_type == xlrd.XL_CELL_DATE:
                value = xldate_as_datetime(value, datemode)
            values.append(value)
        return values

    @api.model
    def _normalize_label(self, value):
        value = unicodedata.normalize("NFKD", str(value))
        value = "".join(
            character for character in value if not unicodedata.combining(character)
        )
        return " ".join(value.split()).casefold()
