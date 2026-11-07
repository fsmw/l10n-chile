# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Extract explicit, reviewable facts from an immutable SET case snapshot."""

import re
import unicodedata
from decimal import Decimal

INTERPRETER_VERSION = "sii_case_instructions_v2"
_COLUMN_NAMES = {
    "CANTIDAD": "quantity",
    "PRECIO UNITARIO": "unit_price",
    "VALOR UNITARIO": "unit_price",
    "UNIDAD MEDIDA": "uom",
    "UNIDAD DE MEDIDA": "uom",
    "DESCUENTO ITEM": "discount_percent",
}
_NOTE_REASONS = {
    "ANULA FACTURA": ("cancel", "1", "33"),
    "ANULA NOTA DE CREDITO": ("cancel", "1", "61"),
    "DEVOLUCION DE MERCADERIAS": ("return", "3", "33"),
    "CORRIGE GIRO": ("text", "2", "33"),
    "CORRIGE GIRO DEL RECEPTOR": ("text", "2", "33"),
    "CORRIGE TEXTO": ("text", "2", "33"),
    "CORRIGE MONTO": ("amount", "3", "33"),
    "MODIFICA MONTO": ("amount", "3", "33"),
}


def _label(value):
    plain = "".join(
        char
        for char in unicodedata.normalize("NFD", value.upper())
        if unicodedata.category(char) != "Mn"
    )
    return re.sub(r"\s+", " ", plain).strip()


def _cells(line):
    return [
        value.strip() for value in re.split(r"\t+| {2,}", line.strip()) if value.strip()
    ]


def _validate_item_values(item, diagnostics):
    """Flag explicit decimal values that cannot be used in a DTE detail."""
    limits = {
        "quantity": (Decimal("0"), None, False),
        "unit_price": (Decimal("0"), None, True),
        "discount_percent": (Decimal("0"), Decimal("100"), True),
    }
    for field_name, (minimum, maximum, inclusive_minimum) in limits.items():
        value = item.get(field_name)
        if value is None:
            continue
        amount = Decimal(value)
        if amount.is_finite():
            valid_minimum = amount >= minimum if inclusive_minimum else amount > minimum
            valid_maximum = maximum is None or amount <= maximum
        else:
            valid_minimum = valid_maximum = False
        if not valid_minimum or not valid_maximum:
            diagnostics.append(
                {
                    "code": "item_value_out_of_range",
                    "source_line": item["source_line"],
                    "field": field_name,
                    "value": value,
                }
            )


def interpret(instruction_text, document_code, source_start_line):  # noqa: C901
    """Return explicit facts and diagnostics without completing missing values."""
    lines = instruction_text.splitlines()
    result = {
        "interpreter_version": INTERPRETER_VERSION,
        "items": [],
        "global_discount_percent": None,
        "global_discount_source_line": None,
        "reference_case_identifier": None,
        "reference_source_line": None,
        "reference_reason": None,
        "reference_reason_source_line": None,
        "note_operation": None,
        "reference_code": None,
        "reference_document_code": None,
        "reference_code_source_line": None,
        "text_correction": [],
        "diagnostics": [],
    }
    supported = document_code in {"33", "34", "56", "61"}
    if not supported:
        result["diagnostics"].append(
            {"code": "unsupported_document_type", "source_line": source_start_line}
        )
        result["state"] = "review_required"
        return result

    columns = None
    for offset, line in enumerate(lines):
        source_line = source_start_line + offset
        label = _label(line)
        stripped = line.strip()
        if not stripped or re.fullmatch(r"[-=]+", stripped):
            continue
        if re.match(r"^CASO\s+\d+-\d+\s*$", label) or re.match(r"^DOCUMENTO\b", label):
            continue
        if re.match(r"^REFERENCIA\b", label):
            match = re.search(r"CASO\s+(\d+-\d+)\b", label)
            if match:
                result["reference_case_identifier"] = match.group(1)
                result["reference_source_line"] = source_line
            else:
                result["diagnostics"].append(
                    {
                        "code": "unparsed_reference",
                        "source_line": source_line,
                        "text": line,
                    }
                )
            continue
        if re.match(r"^RAZON REFERENCIA\b", label):
            result["reference_reason"] = re.sub(
                r"^\s*RAZ[ÓO]N\s+REFERENCIA\s*:?\s*", "", line, flags=re.I
            )
            result["reference_reason_source_line"] = source_line
            normalized_reason = _label(result["reference_reason"])
            reason = _NOTE_REASONS.get(normalized_reason)
            if reason:
                (
                    result["note_operation"],
                    result["reference_code"],
                    result["reference_document_code"],
                ) = reason
                result["reference_code_source_line"] = source_line
            else:
                result["diagnostics"].append(
                    {
                        "code": "note_reason_unsupported",
                        "source_line": source_line,
                        "text": result["reference_reason"],
                    }
                )
            continue
        discount = re.search(r"DESCUENTO GLOBAL[^%]*?(\d+(?:\.\d+)?)\s*%", label)
        if discount:
            result["global_discount_percent"] = discount.group(1)
            result["global_discount_source_line"] = source_line
            continue
        if (
            re.match(r"^\s*(?:DONDE\s+)?DICE\s*: ?", line, re.I)
            or re.match(r"^\s*DEBE\s+DECIR\s*: ?", line, re.I)
            or re.match(r"^\s*(?:DONDE\s+)?DICE\s*:?$", line, re.I)
            or re.match(r"^\s*DEBE\s+DECIR\s*:?$", line, re.I)
        ):
            result["text_correction"].append({"source_line": source_line, "text": line})
            continue

        cells = _cells(line)
        if cells and _label(cells[0]) == "ITEM":
            mapped = [_COLUMN_NAMES.get(_label(value)) for value in cells[1:]]
            if mapped and all(mapped):
                columns = mapped
                continue
        if columns and cells:
            if len(cells) == len(columns) + 2 and re.fullmatch(
                r"ITEM\s+\d+", cells[0], re.I
            ):
                cells = cells[1:]
            if len(cells) == len(columns) + 1:
                item_name = cells[0]
                values = dict(zip(columns, cells[1:], strict=True))
                numeric_fields = ("quantity", "unit_price", "discount_percent")
                invalid_number = False
                for key in numeric_fields:
                    if key in values:
                        values[key] = values[key].removesuffix("%")
                        if not re.fullmatch(r"\d+(?:\.\d+)?", values[key]):
                            invalid_number = True
                if item_name and not invalid_number:
                    item = {
                        "sequence": len(result["items"]) + 1,
                        "source_line": source_line,
                        "name": item_name,
                        "quantity": values.get("quantity"),
                        "unit_price": values.get("unit_price"),
                        "uom": values.get("uom"),
                        "discount_percent": values.get("discount_percent"),
                    }
                    _validate_item_values(item, result["diagnostics"])
                    result["items"].append(item)
                    continue
        result["diagnostics"].append(
            {
                "code": "uninterpreted_line",
                "source_line": source_line,
                "text": line,
            }
        )

    if document_code in {"33", "34"} and not result["items"]:
        result["diagnostics"].append(
            {
                "code": "detail_table_missing_or_unrecognized",
                "source_line": source_start_line,
            }
        )
    if document_code in {"56", "61"}:
        if not result["reference_case_identifier"]:
            result["diagnostics"].append(
                {
                    "code": "note_reference_missing_or_unrecognized",
                    "source_line": source_start_line,
                }
            )
        if not result["reference_reason"] or not result["note_operation"]:
            result["diagnostics"].append(
                {
                    "code": "note_reason_missing_or_unrecognized",
                    "source_line": source_start_line,
                }
            )
        expected_note_code = {
            "cancel": {"56", "61"},
            "return": {"61"},
            "text": {"61"},
            "amount": {"61"},
        }
        if (
            result["note_operation"]
            and document_code not in expected_note_code[result["note_operation"]]
        ):
            result["diagnostics"].append(
                {
                    "code": "note_operation_document_type_mismatch",
                    "source_line": result["reference_reason_source_line"],
                    "document_code": document_code,
                    "operation": result["note_operation"],
                }
            )
        expected_note_code = {"33": "61", "61": "56"}.get(
            result["reference_document_code"]
        )
        if expected_note_code and document_code != expected_note_code:
            result["diagnostics"].append(
                {
                    "code": "note_reference_document_type_mismatch",
                    "source_line": result["reference_reason_source_line"],
                    "document_code": document_code,
                    "expected_reference_document_code": result[
                        "reference_document_code"
                    ],
                }
            )
    if result["global_discount_percent"] and document_code != "33":
        result["diagnostics"].append(
            {
                "code": "global_discount_not_supported_for_document_type",
                "source_line": source_start_line,
            }
        )
    if (
        result["global_discount_percent"]
        and Decimal(result["global_discount_percent"]) > 100
    ):
        result["diagnostics"].append(
            {
                "code": "global_discount_out_of_range",
                "source_line": result["global_discount_source_line"],
            }
        )
    if result["text_correction"]:
        markers = [_label(item["text"]) for item in result["text_correction"]]
        has_dice = any(re.match(r"^(DONDE\s+)?DICE\b", value) for value in markers)
        has_debe_decir = any(re.match(r"^DEBE\s+DECIR\b", value) for value in markers)
        if not has_dice or not has_debe_decir:
            result["diagnostics"].append(
                {
                    "code": "incomplete_text_correction",
                    "source_line": result["text_correction"][0]["source_line"],
                }
            )
    result["state"] = "review_required" if result["diagnostics"] else "interpreted"
    return result
