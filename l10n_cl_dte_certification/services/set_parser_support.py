# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Literal constants of the assigned SII set TXT profile."""

import re

from .section_locator import DOCUMENT_TYPES

SCHEMA = "l10n_cl_dte_certification/1"
SECTION_SCHEMA = "l10n_cl_dte_certification/section/1"
SCHEMA_V2 = "l10n_cl_dte_certification/2"
SECTION_SCHEMA_V2 = "l10n_cl_dte_certification/section/2"
ATTENTION = re.compile(r"NUMERO\s+DE\s+ATENCION\s*:\s*(\d+)", re.I)
CASE = re.compile(r"^\s*CASO\s+(\d+)-(\d+)\s*$", re.I)
DOCUMENT = re.compile(r"^\s*DOCUMENTO\s+(.+?)\s*$", re.I)
TABLE = re.compile(
    r"^\s*ITEM(?:\t+| {2,})CANTIDAD(?:\t+| {2,})PRECIO UNITARIO\s*$",
    re.I,
)
ROW = re.compile(
    r"^\s*ITEM\s+(\d+)(?:\t+| {2,})(.+?)(?:\t+| {2,})" r"(\d+)(?:\t+| {2,})(\d+)\s*$",
    re.I,
)
TYPES = {
    "FACTURA ELECTRONICA": "33",
    "FACTURA ELECTRÓNICA": "33",
    "FACTURA NO AFECTA O EXENTA ELECTRONICA": "34",
    "FACTURA NO AFECTA O EXENTA ELECTRÓNICA": "34",
    "NOTA DE DEBITO ELECTRONICA": "61",
    "NOTA DE DÉBITO ELECTRÓNICA": "61",
    "NOTA DE CREDITO ELECTRONICA": "56",
    "NOTA DE CRÉDITO ELECTRÓNICA": "56",
}
TYPES_V2 = {
    **{key: value for key, value in TYPES.items() if not key.startswith("NOTA DE ")},
    "NOTA DE DEBITO ELECTRONICA": "56",
    "NOTA DE DÉBITO ELECTRÓNICA": "56",
    "NOTA DE CREDITO ELECTRONICA": "61",
    "NOTA DE CRÉDITO ELECTRÓNICA": "61",
}
LEGACY_SECTION_TYPES = {
    **{
        key: value
        for key, value in DOCUMENT_TYPES.items()
        if not key.startswith("NOTA DE ")
    },
    "NOTA DE DEBITO ELECTRONICA": "61",
    "NOTA DE CREDITO ELECTRONICA": "56",
}
CASE_KEYS = {
    "identifier",
    "sequence",
    "document_code",
    "source_start_line",
    "source_end_line",
    "instructions",
    "expected_totals",
    "global_discount_percent",
    "reference_case_identifier",
    "note_operation",
    "reference_reason",
    "lines",
    "unsupported_instructions",
}
CASE_KEYS_V2 = CASE_KEYS | {
    "sii_reference_reason",
    "sii_reference_reason_source_lines",
}
LINE_KEYS = {
    "sequence",
    "source_line",
    "name",
    "quantity",
    "unit_price",
    "discount_percent",
    "uom",
    "exempt",
}
EXPECTED_TOTAL_KEYS = {"net", "exempt", "vat_rate", "vat", "total"}
