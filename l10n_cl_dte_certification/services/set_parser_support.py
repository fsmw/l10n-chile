# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Constants and pure helpers of the assigned SII set TXT profile."""

import re
from decimal import Decimal, InvalidOperation

from odoo import _
from odoo.exceptions import ValidationError

from .section_locator import DOCUMENT as SECTION_DOCUMENT
from .section_locator import DOCUMENT_TYPES, normalized_label

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


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(_("Repeated JSON key: %s") % key)
        result[key] = value
    return result


def _bad_constant(value):
    raise ValidationError(_("Non-finite JSON number: %s") % value)


def decimal_value(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+)?", value):
        raise ValidationError(_("Fiscal numbers must be unambiguous decimal strings."))
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ValidationError(_("Invalid fiscal number.")) from error
    if not number.is_finite():
        raise ValidationError(_("Fiscal numbers must be finite."))
    return number


def _parse_note_metadata(block, document_code):
    if document_code not in {"56", "61"}:
        return None, None, None
    normalized = [normalized_label(line) for line in block]
    references = [line for line in normalized if re.match(r"^REFERENCIA\b", line)]
    reasons = [
        re.sub(r"^RAZON REFERENCIA\s*:?\s*", "", line).strip()
        for line in normalized
        if re.match(r"^RAZON REFERENCIA\b", line)
    ]
    if len(references) != 1 or len(reasons) != 1:
        return None, None, None
    match = re.search(r"CASO\s+(\d+-\d+)\b", references[0])
    if not match:
        return None, None, reasons[0]
    reason = reasons[0]
    operation = (
        "cancel"
        if "ANULA" in reason
        else "return"
        if "DEVOLUCION" in reason
        else "text"
        if "CORRIGE GIRO" in reason or "CORRIGE TEXTO" in reason
        else "amount"
        if "MODIFICA MONTO" in reason
        else None
    )
    return match.group(1), operation, reasons[0]


def _parse_text_correction(block):
    """Return exact correction lines and their relative positions in the case."""
    lines = []
    for index, line in enumerate(block):
        if re.match(r"^\s*(?:DONDE\s+)?DICE\s*:?", line, re.I) or re.match(
            r"^\s*DEBE\s+DECIR\s*:?", line, re.I
        ):
            lines.append((index, line))
    if not any(
        re.match(r"^\s*(?:DONDE\s+)?DICE\s*:?", line, re.I) for _, line in lines
    ):
        return None
    if not any(re.match(r"^\s*DEBE\s+DECIR\s*:?", line, re.I) for _, line in lines):
        return None
    return "\n".join(line for _, line in lines)


def _text_correction_source_lines(block, source_start_line):
    return [
        source_start_line + index
        for index, line in enumerate(block)
        if re.match(r"^\s*(?:DONDE\s+)?DICE", line, re.I)
        or re.match(r"^\s*DEBE\s+DECIR", line, re.I)
    ]


def _is_section_table_header(label):
    if not label.startswith("ITEM "):
        return False
    headers = label.removeprefix("ITEM ").split(" ")
    return any(
        marker in headers for marker in ("CANTIDAD", "UNITARIO", "MEDIDA", "ITEM")
    ) and any(marker in label for marker in ("CANTIDAD", "UNITARIO"))


def _parse_section_details(lines, start, stop, document_code, *, version=1):  # noqa: C901
    """Read the assigned case's explicit line table without inferring missing values."""
    items = []
    unsupported = []
    columns = None
    global_discount = None
    covered = set()
    column_names = {
        "CANTIDAD": "quantity",
        "PRECIO UNITARIO": "unit_price",
        "VALOR UNITARIO": "unit_price",
        "UNIDAD MEDIDA": "uom",
        "UNIDAD DE MEDIDA": "uom",
        "DESCUENTO ITEM": "discount_percent",
    }
    for index in range(start + 1, stop):
        line = lines[index]
        label = normalized_label(line)
        discount_match = re.search(r"DESCUENTO GLOBAL[^%]*?(\d+(?:\.\d+)?)\s*%", label)
        if discount_match:
            global_discount = discount_match.group(1)
            covered.add(index + 1)
            continue
        cells = [
            part.strip()
            for part in re.split(r"\t+| {2,}", line.strip())
            if part.strip()
        ]
        if cells and normalized_label(cells[0]) == "ITEM":
            headers = [normalized_label(value) for value in cells[1:]]
            mapped = [column_names.get(header) for header in headers]
            if mapped and all(mapped):
                columns = mapped
                covered.add(index + 1)
                continue
        if (
            not line.strip()
            or re.fullmatch(r"[-=]+", line.strip())
            or SECTION_DOCUMENT.match(label)
            or re.match(r"^(REFERENCIA|RAZON REFERENCIA)\b", label)
            or version >= 2
            and (
                re.match(r"^\s*(?:DONDE\s+)?DICE\s*:?", line, re.I)
                or re.match(r"^\s*DEBE\s+DECIR\s*:?", line, re.I)
            )
        ):
            covered.add(index + 1)
            continue
        if not columns or not cells or not line.strip():
            unsupported.append({"source_line": index + 1, "text": line})
            continue
        if len(cells) == len(columns) + 2 and re.fullmatch(
            r"ITEM\s+\d+", cells[0], re.I
        ):
            cells = cells[1:]
        if len(cells) != len(columns) + 1:
            unsupported.append({"source_line": index + 1, "text": line})
            covered.add(index + 1)
            continue
        item_name = cells[0]
        row = dict(zip(columns, cells[1:], strict=True))
        if "quantity" in row and not re.fullmatch(r"\d+(?:\.\d+)?", row["quantity"]):
            unsupported.append({"source_line": index + 1, "text": line})
            covered.add(index + 1)
            continue
        if "unit_price" in row and not re.fullmatch(
            r"\d+(?:\.\d+)?", row["unit_price"]
        ):
            unsupported.append({"source_line": index + 1, "text": line})
            covered.add(index + 1)
            continue
        if "discount_percent" in row:
            row["discount_percent"] = row["discount_percent"].removesuffix("%")
            if not re.fullmatch(r"\d+(?:\.\d+)?", row["discount_percent"]):
                unsupported.append({"source_line": index + 1, "text": line})
                covered.add(index + 1)
                continue
        items.append(
            {
                "sequence": len(items) + 1,
                "source_line": index + 1,
                "name": item_name,
                "quantity": row.get("quantity"),
                "unit_price": row.get("unit_price"),
                "discount_percent": row.get("discount_percent", "0"),
                "uom": row.get("uom"),
                "exempt": document_code == "34"
                or "EXENTO" in normalized_label(cells[0]),
            }
        )
        covered.add(index + 1)
    unsupported = list({item["source_line"]: item for item in unsupported}.values())
    for index in range(start + 1, stop):
        if index + 1 not in covered:
            unsupported.append({"source_line": index + 1, "text": lines[index]})
    if document_code in {"33", "34"} and not items:
        unsupported.append(
            {"source_line": start + 1, "text": "Detail table not recognized"}
        )
    if len(items) > 60:
        unsupported.append({"source_line": start + 1, "text": "More than 60 details"})
    return items, unsupported, global_discount
