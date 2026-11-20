# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Parse and validate an RCV detail CSV before anything is stored."""

import csv
import io
import unicodedata
from dataclasses import dataclass

from odoo.exceptions import UserError

from .rcv_values import is_valid_rut, normalize_vat, parse_date, to_integer

MAX_CSV_BYTES = 10 * 1024 * 1024
AMOUNT_COLUMNS = (
    "net_amount",
    "exempt_amount",
    "vat_amount",
    "vat_not_recoverable",
    "common_vat",
    "total_amount",
)
REQUIRED_COLUMNS = (
    "document_type_code",
    "counterpart_vat",
    "folio",
    "document_date",
    "net_amount",
    "exempt_amount",
    "vat_amount",
    "total_amount",
)
BREAKDOWN_COLUMNS = ("vat_amount", "vat_not_recoverable", "common_vat")
# Accepted headers, compared without case, accents or underscores. The first
# name of each column is the documented layout; the rest are the headers of
# the purchase and sales detail files downloaded from the SII RCV portal.
HEADER_ALIASES = {
    "document_type_code": ("tipo documento", "tipo doc"),
    "counterpart_vat": ("rut contraparte", "rut proveedor", "rut cliente"),
    "counterpart_name": ("razon social",),
    "folio": ("folio",),
    "document_date": ("fecha", "fecha docto"),
    "net_amount": ("neto", "monto neto"),
    "exempt_amount": ("exento", "monto exento"),
    "vat_amount": ("iva", "monto iva recuperable", "monto iva"),
    "vat_not_recoverable": ("iva no recuperable", "monto iva no recuperable"),
    "common_vat": ("iva uso comun",),
    "total_amount": ("total", "monto total"),
}


class RcvCsvRowError(ValueError):
    """A CSV row that cannot be imported, with a translated reason."""


@dataclass(frozen=True)
class RcvCsvResult:
    """Valid rows and per-line errors of one CSV file."""

    rows: tuple
    errors: tuple


def _header_key(header):
    text = unicodedata.normalize("NFKD", str(header or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.replace("_", " ").replace(".", " ").lower().split())


class RcvCsvParser:
    """Turn the bytes of an RCV CSV into validated document rows.

    File-level problems (size, encoding, header) raise ``UserError``. A row
    that cannot be imported is never dropped silently: it is reported in
    ``errors`` with its line number.
    """

    def __init__(self, env):
        self.env = env

    def parse(self, content):
        reader = csv.reader(
            io.StringIO(self._decode(content)), delimiter=self._delimiter(content)
        )
        header = next(reader, None)
        columns = self._columns(header)
        rows, errors, seen = [], [], set()
        for line, cells in enumerate(reader, start=2):
            if not any(cell.strip() for cell in cells):
                continue
            try:
                rows.append(self._row(columns, cells, seen))
            except RcvCsvRowError as error:
                errors.append(
                    self.env._("Line %(line)s: %(reason)s", line=line, reason=error)
                )
        if not rows and not errors:
            raise UserError(self.env._("The CSV file has no document rows."))
        return RcvCsvResult(rows=tuple(rows), errors=tuple(errors))

    def _decode(self, content):
        if not content:
            raise UserError(self.env._("The CSV file is empty."))
        if len(content) > MAX_CSV_BYTES:
            raise UserError(self.env._("The CSV file cannot be larger than 10 MB."))
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError:
            # Files downloaded from the SII portal may be Latin-1 encoded.
            return content.decode("latin-1")

    def _delimiter(self, content):
        first_line = content.split(b"\n", 1)[0]
        return ";" if first_line.count(b";") > first_line.count(b",") else ","

    def _columns(self, header):
        keys = [_header_key(cell) for cell in header or []]
        columns = {}
        for column, aliases in HEADER_ALIASES.items():
            for alias in aliases:
                if alias in keys:
                    columns[column] = keys.index(alias)
                    break
        missing = [column for column in REQUIRED_COLUMNS if column not in columns]
        if missing:
            expected = ", ".join(HEADER_ALIASES[column][0] for column in missing)
            raise UserError(
                self.env._(
                    "The CSV file is missing these columns: %(columns)s.",
                    columns=expected,
                )
            )
        return columns

    def _row(self, columns, cells, seen):
        def cell(column):
            index = columns.get(column)
            if index is None or index >= len(cells):
                return ""
            return cells[index].strip()

        values = {
            "document_type_code": self._digits(cell("document_type_code"), "type"),
            "folio": self._digits(cell("folio"), "folio"),
            "counterpart_vat": self._rut(cell("counterpart_vat")),
            "counterpart_name": cell("counterpart_name"),
            "document_date": self._date(cell("document_date")),
            "tax_breakdown_available": all(cell(name) for name in BREAKDOWN_COLUMNS),
        }
        for column in AMOUNT_COLUMNS:
            values[column] = self._amount(cell(column), column)
        identity = (
            values["document_type_code"],
            values["counterpart_vat"],
            values["folio"],
        )
        if identity in seen:
            raise RcvCsvRowError(
                self.env._("the document appears more than once in the file.")
            )
        seen.add(identity)
        return values

    def _digits(self, value, kind):
        if not value.isdigit() or not int(value):
            if kind == "type":
                message = self.env._("missing or invalid document type '%s'.", value)
            else:
                message = self.env._("missing or invalid folio '%s'.", value)
            raise RcvCsvRowError(message)
        return str(int(value))

    def _rut(self, value):
        vat = normalize_vat(value)
        if not is_valid_rut(vat):
            raise RcvCsvRowError(
                self.env._("invalid counterpart RUT '%s'.", value),
            )
        return vat

    def _date(self, value):
        if not value:
            return False
        parsed = parse_date(value)
        if not parsed:
            raise RcvCsvRowError(self.env._("invalid document date '%s'.", value))
        return parsed

    def _amount(self, value, column):
        try:
            return to_integer(value)
        except ValueError as error:
            raise RcvCsvRowError(
                self.env._(
                    "invalid amount '%(value)s' in column %(column)s.",
                    value=value,
                    column=column,
                )
            ) from error
