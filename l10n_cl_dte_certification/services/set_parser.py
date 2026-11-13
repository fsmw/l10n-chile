# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Strict import of assigned SII text and reviewed mappings."""

import hashlib
import json
import re

from odoo import _
from odoo.exceptions import ValidationError

from .section_locator import CASE as SECTION_CASE
from .section_locator import DOCUMENT as SECTION_DOCUMENT
from .section_locator import locate_sections, normalized_label
from .set_parser_support import (
    ATTENTION,
    CASE,
    CASE_KEYS,
    CASE_KEYS_V2,
    DOCUMENT,
    EXPECTED_TOTAL_KEYS,
    LEGACY_SECTION_TYPES,
    LINE_KEYS,
    ROW,
    SCHEMA,
    SCHEMA_V2,
    SECTION_SCHEMA,
    SECTION_SCHEMA_V2,
    TABLE,
    TYPES,
    TYPES_V2,
    _bad_constant,
    _is_section_table_header,
    _parse_note_metadata,
    _parse_section_details,
    _parse_text_correction,
    _text_correction_source_lines,
    _unique_json,
    decimal_value,
)


def _validate_expected_totals(expected):
    if set(expected) - EXPECTED_TOTAL_KEYS:
        raise ValidationError(_("Invalid mapped fiscal case."))
    for value in expected.values():
        if value is not None:
            decimal_value(value)


def _validate_case_note_metadata(item):
    code = item["document_code"]
    reference = item["reference_case_identifier"]
    operation = item["note_operation"]
    reason = item["reference_reason"]
    if code in {"56", "61"}:
        if (
            not isinstance(reference, str)
            or not re.fullmatch(r"\d+-\d+", reference)
            or operation not in {"cancel", "return", "text", "amount"}
            or not isinstance(reason, str)
            or not reason
        ):
            raise ValidationError(_("A DTE note requires a reviewed reference."))
    elif any(value is not None for value in (reference, operation, reason)):
        raise ValidationError(_("An invoice cannot include note reference data."))
    discount = item["global_discount_percent"]
    if discount is not None:
        value = decimal_value(discount)
        if code != "33" or value <= 0 or value > 100:
            raise ValidationError(_("Invalid global discount mapping."))


def _validate_v2_note_content(item, block, source_start_line):
    code = item["document_code"]
    operation = item["note_operation"]
    source_lines = item["sii_reference_reason_source_lines"]
    if not isinstance(source_lines, list) or any(
        type(line) is not int for line in source_lines
    ):
        raise ValidationError(_("Invalid correction text source range."))
    if code in {"56", "61"}:
        allowed = {"61": {"text", "return", "cancel"}, "56": {"cancel"}}
        if operation not in allowed[code]:
            raise ValidationError(_("The note operation does not match its DTE type."))
        if operation == "text":
            source_text = _parse_text_correction(block)
            source_lines = _text_correction_source_lines(block, source_start_line)
            if (
                not source_text
                or item["sii_reference_reason"] != source_text
                or source_lines
                != _text_correction_source_lines(block, source_start_line)
            ):
                raise ValidationError(_("Text correction must match the original TXT."))
        elif item["sii_reference_reason"] not in (None, False, "") or source_lines:
            raise ValidationError(
                _("Only text corrections may contain correction text.")
            )
    elif item["sii_reference_reason"] not in (None, False, "") or source_lines:
        raise ValidationError(_("An invoice cannot contain correction text."))


def _add_v2_reference_diagnostics(cases):
    by_identifier = {case["identifier"]: case for case in cases}
    for case in cases:
        if case["document_code"] not in {"56", "61"}:
            continue
        target = by_identifier.get(case["reference_case_identifier"])
        expected_type = "61" if case["document_code"] == "56" else "33"
        if (
            not target
            or target["sequence"] >= case["sequence"]
            or target["document_code"] != expected_type
            or (case["document_code"] == "56" and target["note_operation"] != "text")
        ):
            case["unsupported_instructions"].append(
                {
                    "source_line": case["source_start_line"],
                    "text": (
                        "Referenced case is missing, later, or has an incompatible "
                        "DTE type"
                    ),
                }
            )


def _number_occurs(value, original):
    variants = (value, value.replace(".", ","))
    return any(
        re.search(rf"(?<![\w.,]){re.escape(item)}(?![\w.,])", original)
        for item in variants
    )


def _check_unique_case_identifiers(identifiers):
    identifiers = list(identifiers)
    if len(set(identifiers)) != len(identifiers):
        raise ValidationError(_("The TXT repeats a case identifier."))


class CertificationSetParser:
    """Parse a narrow TXT profile; never silently infer fiscal case values."""

    def parse(
        self,
        source_bytes,
        encoding,
        profile,
        mapping_bytes=None,
        selected_attention=None,
    ):
        if (
            not isinstance(source_bytes, bytes)
            or not 0 < len(source_bytes) <= 2 * 1024 * 1024
        ):
            raise ValidationError(_("Upload a non-empty TXT below 2 MiB."))
        if source_bytes.startswith((b"PK\x03\x04", b"%PDF")) or b"\x00" in source_bytes:
            raise ValidationError(_("The original SII set must be TXT."))
        if encoding not in {"utf-8", "iso-8859-1"}:
            raise ValidationError(_("Unsupported SII set encoding."))
        try:
            text = source_bytes.decode(encoding)
        except UnicodeDecodeError as error:
            raise ValidationError(
                _("The selected encoding does not match the TXT.")
            ) from error
        if text.startswith("\ufeff"):
            if encoding != "utf-8":
                raise ValidationError(_("A UTF-8 BOM requires UTF-8 encoding."))
            text = text[1:]
        if text.lstrip().startswith(("<", "{", "[")):
            raise ValidationError(_("The original SII set must be TXT."))
        if profile in {"sii_txt_v1", "sii_txt_v2"}:
            if mapping_bytes:
                raise ValidationError(_("TXT profile cannot use a JSON mapping."))
            return self._parse_txt(
                text, source_bytes, version=2 if profile.endswith("v2") else 1
            )
        if profile in {"sii_section_v1", "sii_section_v2"}:
            if mapping_bytes:
                raise ValidationError(_("TXT profile cannot use a JSON mapping."))
            return self._parse_section(
                text,
                source_bytes,
                selected_attention,
                version=2 if profile.endswith("v2") else 1,
            )
        if (
            profile
            not in {
                "reviewed_mapping_v1",
                "reviewed_section_v1",
                "reviewed_mapping_v2",
                "reviewed_section_v2",
            }
            or not isinstance(mapping_bytes, bytes)
            or not 0 < len(mapping_bytes) <= 2 * 1024 * 1024
        ):
            raise ValidationError(_("A reviewed JSON mapping below 2 MiB is required."))
        try:
            payload = json.loads(
                mapping_bytes.decode("utf-8"),
                object_pairs_hook=_unique_json,
                parse_constant=_bad_constant,
            )
        except (UnicodeError, ValueError) as error:
            raise ValidationError(
                _("The reviewed mapping must be UTF-8 JSON.")
            ) from error
        if profile in {"reviewed_section_v1", "reviewed_section_v2"}:
            return self.validate_section_mapping(
                source_bytes,
                payload,
                encoding,
                selected_attention,
                version=2 if profile.endswith("v2") else 1,
            )
        return self.validate_mapping(
            source_bytes, payload, encoding, version=2 if profile.endswith("v2") else 1
        )

    def _parse_section(self, text, source_bytes, selected_attention, *, version=1):
        attention = (selected_attention or "").strip()
        if not attention.isdigit():
            raise ValidationError(_("Choose a SET attention number from the TXT."))
        matches = [
            section
            for section in locate_sections(text)
            if section["attention_number"] == attention
        ]
        if len(matches) != 1 or not matches[0]["case_count"]:
            raise ValidationError(_("Choose a SET section that contains cases."))
        section = matches[0]
        lines = text.splitlines()
        anchors = [
            index
            for index in range(section["start_line"], section["end_line"])
            if SECTION_CASE.match(normalized_label(lines[index]))
        ]
        cases = []
        for ordinal, start in enumerate(anchors, 1):
            stop = anchors[ordinal] if ordinal < len(anchors) else section["end_line"]
            block = lines[start:stop]
            labels = [
                match.group(1)
                for line in block
                if (match := SECTION_DOCUMENT.match(normalized_label(line)))
            ]
            if len(labels) != 1:
                raise ValidationError(_("Every case requires one DOCUMENTO line."))
            code = (LEGACY_SECTION_TYPES if version == 1 else TYPES_V2).get(
                labels[0], "unsupported"
            )
            items, unsupported, global_discount = _parse_section_details(
                lines, start, stop, code, version=version
            )
            reference_case, note_operation, reference_reason = _parse_note_metadata(
                block, code
            )
            correction_text = _parse_text_correction(block) if version >= 2 else None
            correction_source_lines = (
                _text_correction_source_lines(block, start + 1)
                if version >= 2 and correction_text
                else []
            )
            if version >= 2 and note_operation == "text" and not correction_text:
                unsupported.append(
                    {
                        "source_line": start + 1,
                        "text": "Text correction content not recognized",
                    }
                )
            if (
                version >= 2
                and code in {"56", "61"}
                and (not reference_case or not note_operation or not reference_reason)
            ):
                unsupported.append(
                    {"source_line": start + 1, "text": "Note operation not recognized"}
                )
            if code in {"56", "61"} and not (
                reference_case and note_operation and reference_reason
            ):
                unsupported.append(
                    {"source_line": start + 1, "text": "Note reference not recognized"}
                )
            number = SECTION_CASE.match(normalized_label(lines[start])).group(2)
            cases.append(
                {
                    "identifier": f"{attention}-{number}",
                    "sequence": ordinal,
                    "document_code": code,
                    "source_start_line": start + 1,
                    "source_end_line": stop,
                    "instructions": "\n".join(block),
                    "expected_totals": {},
                    "global_discount_percent": global_discount,
                    "reference_case_identifier": reference_case,
                    "note_operation": note_operation,
                    "reference_reason": reference_reason,
                    **(
                        {"sii_reference_reason": correction_text}
                        if version >= 2
                        else {}
                    ),
                    **(
                        {"sii_reference_reason_source_lines": correction_source_lines}
                        if version >= 2
                        else {}
                    ),
                    "lines": items,
                    "unsupported_instructions": unsupported,
                }
            )
        if version >= 2:
            _add_v2_reference_diagnostics(cases)
        return {
            "schema_version": SECTION_SCHEMA_V2 if version >= 2 else SECTION_SCHEMA,
            "source_digest": hashlib.sha256(source_bytes).hexdigest(),
            "attention_number": attention,
            "section_start_line": section["start_line"],
            "section_end_line": section["end_line"],
            "cases": cases,
            "diagnostics": [],
        }

    def _parse_txt(self, text, source_bytes, *, version=1):  # noqa: C901
        lines = text.splitlines()
        attentions = {m.group(1) for line in lines if (m := ATTENTION.search(line))}
        if len(attentions) != 1:
            raise ValidationError(_("The TXT requires one SII attention number."))
        attention = attentions.pop()
        anchors = [
            (i, m.group(1), m.group(2))
            for i, line in enumerate(lines)
            if (m := CASE.match(line))
        ]
        if not 0 < len(anchors) <= 500:
            raise ValidationError(_("The TXT requires between one and 500 cases."))
        _check_unique_case_identifiers(
            f"{case_attention}-{number}"
            for _case_index, case_attention, number in anchors
        )
        cases = []
        for ordinal, (start, case_attention, number) in enumerate(anchors, 1):
            if case_attention != attention:
                raise ValidationError(_("A case has a different attention number."))
            stop = anchors[ordinal][0] if ordinal < len(anchors) else len(lines)
            block = lines[start:stop]
            labels = [
                m.group(1).upper() for line in block if (m := DOCUMENT.match(line))
            ]
            if len(labels) != 1:
                raise ValidationError(_("Every case requires one DOCUMENTO line."))
            code = (TYPES if version == 1 else TYPES_V2).get(labels[0], "unsupported")
            items, unsupported, table = [], [], False
            for line_number, line in enumerate(block[1:], start + 2):
                if DOCUMENT.match(line) or not line.strip():
                    continue
                if version >= 2 and (
                    normalized_label(line).startswith("REFERENCIA ")
                    or normalized_label(line).startswith("RAZON REFERENCIA ")
                    or re.match(r"^\s*(?:DONDE\s+)?DICE\s*:?", line, re.I)
                    or re.match(r"^\s*DEBE\s+DECIR\s*:?", line, re.I)
                ):
                    continue
                if TABLE.match(line):
                    table = True
                    continue
                match = ROW.match(line) if table else None
                if not match:
                    unsupported.append({"source_line": line_number, "text": line})
                    continue
                quantity = match.group(3)
                price = match.group(4)
                decimal_value(quantity)
                decimal_value(price)
                items.append(
                    {
                        "sequence": int(match.group(1)),
                        "source_line": line_number,
                        "name": match.group(2),
                        "quantity": quantity,
                        "unit_price": price,
                        "discount_percent": "0",
                        "uom": None,
                        "exempt": code == "34",
                    }
                )
            if code in {"33", "34"} and (not table or not items):
                unsupported.append(
                    {"source_line": start + 1, "text": "Detail table not recognized"}
                )
            if version >= 2 and code in {"56", "61"}:
                items, detail_diagnostics, _parsed_discount = _parse_section_details(
                    lines, start, stop, code, version=2
                )
                unsupported = detail_diagnostics
            if len(items) > 60:
                unsupported.append(
                    {"source_line": start + 1, "text": "More than 60 details"}
                )
            reference_case = note_operation = reference_reason = correction_text = None
            if version >= 2:
                reference_case, note_operation, reference_reason = _parse_note_metadata(
                    block, code
                )
                correction_text = _parse_text_correction(block)
                correction_source_lines = (
                    _text_correction_source_lines(block, start + 1)
                    if correction_text
                    else []
                )
                if code in {"56", "61"} and (
                    not reference_case or not note_operation or not reference_reason
                ):
                    unsupported.append(
                        {
                            "source_line": start + 1,
                            "text": "Note operation not recognized",
                        }
                    )
                if note_operation == "text" and not correction_text:
                    unsupported.append(
                        {
                            "source_line": start + 1,
                            "text": "Text correction content not recognized",
                        }
                    )
            positions = [item["sequence"] for item in items]
            if len(set(positions)) != len(positions):
                raise ValidationError(_("The TXT repeats a detail position."))
            cases.append(
                {
                    "identifier": f"{attention}-{number}",
                    "sequence": ordinal,
                    "document_code": code,
                    "source_start_line": start + 1,
                    "source_end_line": stop,
                    "instructions": "\n".join(block),
                    "expected_totals": {},
                    "global_discount_percent": None,
                    "reference_case_identifier": reference_case,
                    "note_operation": note_operation,
                    "reference_reason": reference_reason,
                    **(
                        {"sii_reference_reason": correction_text}
                        if version >= 2
                        else {}
                    ),
                    **(
                        {"sii_reference_reason_source_lines": correction_source_lines}
                        if version >= 2
                        else {}
                    ),
                    "lines": items,
                    "unsupported_instructions": unsupported,
                }
            )
        if version >= 2:
            _add_v2_reference_diagnostics(cases)
        return {
            "schema_version": SCHEMA_V2 if version >= 2 else SCHEMA,
            "source_digest": hashlib.sha256(source_bytes).hexdigest(),
            "attention_number": attention,
            "cases": cases,
            "diagnostics": [],
        }

    def validate_mapping(  # noqa: C901
        self, source_bytes, payload, encoding="utf-8", *, version=1
    ):
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "source_digest",
            "attention_number",
            "cases",
        }:
            raise ValidationError(_("Invalid reviewed mapping structure."))
        if (
            payload["schema_version"] != (SCHEMA_V2 if version >= 2 else SCHEMA)
            or payload["source_digest"] != hashlib.sha256(source_bytes).hexdigest()
        ):
            raise ValidationError(_("The mapping does not match the original TXT."))
        lines = source_bytes.decode(encoding).lstrip("\ufeff").splitlines()
        attention = str(payload["attention_number"])
        if not attention.isdigit() or {
            m.group(1) for line in lines if (m := ATTENTION.search(line))
        } != {attention}:
            raise ValidationError(_("Invalid mapping attention number."))
        anchors = [
            (i + 1, f"{m.group(1)}-{m.group(2)}")
            for i, line in enumerate(lines)
            if (m := CASE.match(line))
        ]
        _check_unique_case_identifiers(identifier for _, identifier in anchors)
        cases = payload["cases"]
        if (
            not isinstance(cases, list)
            or len(cases) != len(anchors)
            or not 0 < len(cases) <= 500
        ):
            raise ValidationError(_("Mapping must include every TXT case."))
        for ordinal, (item, (start, identifier)) in enumerate(
            zip(cases, anchors, strict=True), 1
        ):
            stop = anchors[ordinal][0] - 1 if ordinal < len(anchors) else len(lines)
            expected_keys = CASE_KEYS_V2 if version >= 2 else CASE_KEYS
            if not isinstance(item, dict) or set(item) != expected_keys:
                raise ValidationError(_("Invalid mapped case structure."))
            if (
                item["identifier"] != identifier
                or type(item["sequence"]) is not int
                or item["sequence"] != ordinal
                or type(item["source_start_line"]) is not int
                or item["source_start_line"] != start
                or type(item["source_end_line"]) is not int
                or item["source_end_line"] != stop
                or item["instructions"] != "\n".join(lines[start - 1 : stop])
            ):
                raise ValidationError(_("Mapped case does not match its TXT block."))
            if (
                item["document_code"] not in {"33", "34", "56", "61", "unsupported"}
                or not isinstance(item["expected_totals"], dict)
                or not isinstance(item["unsupported_instructions"], list)
                or not isinstance(item["lines"], list)
                or len(item["lines"]) > 60
            ):
                raise ValidationError(_("Invalid mapped fiscal case."))
            _validate_expected_totals(item["expected_totals"])
            _validate_case_note_metadata(item)
            labels = [
                match.group(1).upper()
                for line in lines[start - 1 : stop]
                if (match := DOCUMENT.match(line))
            ]
            types = TYPES if version == 1 else TYPES_V2
            if len(labels) != 1 or item["document_code"] != types.get(
                labels[0], "unsupported"
            ):
                raise ValidationError(_("Mapped DTE type differs from the TXT."))
            if version >= 2:
                _validate_v2_note_content(item, lines[start - 1 : stop], start)
            used_lines = {start}
            for number in range(start + 1, stop + 1):
                if (
                    DOCUMENT.match(lines[number - 1])
                    or TABLE.match(lines[number - 1])
                    or _is_section_table_header(normalized_label(lines[number - 1]))
                    or not lines[number - 1].strip()
                    or version >= 2
                    and (
                        re.match(
                            r"^(REFERENCIA|RAZON REFERENCIA)\b",
                            normalized_label(lines[number - 1]),
                        )
                        or re.match(
                            r"^\s*(?:DONDE\s+)?DICE\s*:?", lines[number - 1], re.I
                        )
                        or re.match(r"^\s*DEBE\s+DECIR\s*:?", lines[number - 1], re.I)
                    )
                ):
                    used_lines.add(number)
            for position, row in enumerate(item["lines"], 1):
                self._validate_mapped_detail(
                    row, position, start, stop, lines, used_lines
                )
            for diagnostic in item["unsupported_instructions"]:
                if (
                    not isinstance(diagnostic, dict)
                    or set(diagnostic) != {"source_line", "text"}
                    or type(diagnostic["source_line"]) is not int
                    or not start < diagnostic["source_line"] <= stop
                    or diagnostic["text"] != lines[diagnostic["source_line"] - 1]
                    or diagnostic["source_line"] in used_lines
                ):
                    raise ValidationError(_("Invalid unsupported TXT line coverage."))
                used_lines.add(diagnostic["source_line"])
            if used_lines != set(range(start, stop + 1)):
                raise ValidationError(_("The mapping omits original TXT lines."))
        return {**payload, "diagnostics": []}

    def validate_section_mapping(
        self,
        source_bytes,
        payload,
        encoding="utf-8",
        selected_attention=None,
        *,
        version=1,
    ):
        required = {
            "schema_version",
            "source_digest",
            "attention_number",
            "section_start_line",
            "section_end_line",
            "acknowledged_section_lines",
            "cases",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            raise ValidationError(_("Invalid reviewed section mapping structure."))
        if (
            payload["schema_version"]
            != (SECTION_SCHEMA_V2 if version >= 2 else SECTION_SCHEMA)
            or payload["source_digest"] != hashlib.sha256(source_bytes).hexdigest()
            or not isinstance(payload["attention_number"], str)
            or payload["attention_number"] != str(selected_attention or "")
        ):
            raise ValidationError(_("The section mapping does not match the TXT."))
        lines = source_bytes.decode(encoding).lstrip("\ufeff").splitlines()
        sections = [
            section
            for section in locate_sections("\n".join(lines))
            if section["attention_number"] == payload["attention_number"]
        ]
        if len(sections) != 1 or not sections[0]["case_count"]:
            raise ValidationError(_("Choose a SET section that contains cases."))
        section = sections[0]
        if (
            type(payload["section_start_line"]) is not int
            or type(payload["section_end_line"]) is not int
            or payload["section_start_line"] != section["start_line"]
            or payload["section_end_line"] != section["end_line"]
        ):
            raise ValidationError(_("The mapped SET section boundaries changed."))
        anchors = [
            (index + 1, f"{match.group(1)}-{match.group(2)}")
            for index in range(section["start_line"], section["end_line"])
            if (match := SECTION_CASE.match(normalized_label(lines[index])))
        ]
        expected_preamble = [
            {"source_line": number, "text": lines[number - 1]}
            for number in range(section["start_line"] + 1, anchors[0][0])
            if lines[number - 1].strip()
            and not re.fullmatch(r"[-=]+", lines[number - 1].strip())
        ]
        if payload["acknowledged_section_lines"] != expected_preamble:
            raise ValidationError(
                _("Review and acknowledge every original SET instruction line.")
            )
        cases = payload["cases"]
        if (
            not isinstance(cases, list)
            or len(cases) != len(anchors)
            or not 0 < len(cases) <= 500
        ):
            raise ValidationError(_("Mapping must include every TXT case."))
        for ordinal, (item, (start, identifier)) in enumerate(
            zip(cases, anchors, strict=True), 1
        ):
            stop = (
                anchors[ordinal][0] - 1
                if ordinal < len(anchors)
                else section["end_line"]
            )
            self._validate_section_case(
                item, identifier, ordinal, start, stop, lines, version=version
            )
        return {**payload, "diagnostics": []}

    def _validate_section_case(
        self, item, identifier, ordinal, start, stop, lines, *, version=1
    ):
        expected_keys = CASE_KEYS_V2 if version >= 2 else CASE_KEYS
        if not isinstance(item, dict) or set(item) != expected_keys:
            raise ValidationError(_("Invalid mapped case structure."))
        if (
            item["identifier"] != identifier
            or type(item["sequence"]) is not int
            or item["sequence"] != ordinal
            or type(item["source_start_line"]) is not int
            or item["source_start_line"] != start
            or type(item["source_end_line"]) is not int
            or item["source_end_line"] != stop
            or item["instructions"] != "\n".join(lines[start - 1 : stop])
        ):
            raise ValidationError(_("Mapped case does not match its TXT block."))
        if (
            not isinstance(item["expected_totals"], dict)
            or not isinstance(item["unsupported_instructions"], list)
            or not isinstance(item["lines"], list)
            or len(item["lines"]) > 60
        ):
            raise ValidationError(_("Invalid mapped fiscal case."))
        _validate_expected_totals(item["expected_totals"])
        _validate_case_note_metadata(item)
        labels = [
            match.group(1)
            for line in lines[start - 1 : stop]
            if (match := SECTION_DOCUMENT.match(normalized_label(line)))
        ]
        types = LEGACY_SECTION_TYPES if version == 1 else TYPES_V2
        if len(labels) != 1 or item["document_code"] != types.get(
            labels[0], "unsupported"
        ):
            raise ValidationError(_("Mapped DTE type differs from the TXT."))
        if version >= 2:
            _validate_v2_note_content(item, lines[start - 1 : stop], start)
        used_lines = {start}
        for number in range(start + 1, stop + 1):
            source_line = lines[number - 1]
            label = normalized_label(source_line)
            if (
                SECTION_DOCUMENT.match(label)
                or re.match(r"^(REFERENCIA|RAZON REFERENCIA)\b", label)
                or version >= 2
                and (
                    re.match(r"^\s*(?:DONDE\s+)?DICE\s*:?", source_line, re.I)
                    or re.match(r"^\s*DEBE\s+DECIR\s*:?", source_line, re.I)
                )
                or ("DESCUENTO GLOBAL" in label and "%" in label)
                or _is_section_table_header(label)
                or not source_line.strip()
                or re.fullmatch(r"[-=]+", source_line.strip())
            ):
                used_lines.add(number)
        for position, row in enumerate(item["lines"], 1):
            self._validate_mapped_detail(row, position, start, stop, lines, used_lines)
        for diagnostic in item["unsupported_instructions"]:
            if (
                not isinstance(diagnostic, dict)
                or set(diagnostic) != {"source_line", "text"}
                or type(diagnostic["source_line"]) is not int
                or not start < diagnostic["source_line"] <= stop
                or diagnostic["text"] != lines[diagnostic["source_line"] - 1]
                or diagnostic["source_line"] in used_lines
            ):
                raise ValidationError(_("Invalid unsupported TXT line coverage."))
            used_lines.add(diagnostic["source_line"])
        if used_lines != set(range(start, stop + 1)):
            raise ValidationError(_("The mapping omits original TXT lines."))

    def _validate_mapped_detail(self, row, position, start, stop, lines, used_lines):
        if (
            not isinstance(row, dict)
            or set(row) != LINE_KEYS
            or row["sequence"] != position
            or type(row["source_line"]) is not int
            or not start < row["source_line"] <= stop
        ):
            raise ValidationError(_("Invalid mapped detail position."))
        if row["source_line"] in used_lines:
            raise ValidationError(_("A mapped TXT line is repeated."))
        used_lines.add(row["source_line"])
        original = lines[row["source_line"] - 1]
        if (
            not isinstance(row["name"], str)
            or not row["name"]
            or row["name"] not in original
        ):
            raise ValidationError(_("Mapped detail name differs from TXT."))
        quantity = decimal_value(row["quantity"]) if row["quantity"] else None
        price = decimal_value(row["unit_price"]) if row["unit_price"] else None
        discount = decimal_value(row["discount_percent"])
        if (
            (quantity is not None and quantity <= 0)
            or (price is not None and price < 0)
            or not 0 <= discount <= 100
            or type(row["exempt"]) is not bool
            or (row["uom"] is not None and not isinstance(row["uom"], str))
        ):
            raise ValidationError(_("Invalid mapped detail values."))
        if quantity is not None and not _number_occurs(row["quantity"], original):
            raise ValidationError(_("Mapped quantity is absent from TXT."))
        if price is not None and not _number_occurs(row["unit_price"], original):
            raise ValidationError(_("Mapped unit price is absent from TXT."))
        if discount and not _number_occurs(row["discount_percent"], original):
            raise ValidationError(_("Mapped discount is absent from TXT."))

    def canonical_digest(self, payload):
        def check(value):
            if isinstance(value, float):
                raise ValidationError(_("Fiscal snapshots cannot contain floats."))
            if isinstance(value, dict):
                for item in value.values():
                    check(item)
            elif isinstance(value, list):
                for item in value:
                    check(item)

        check(payload)
        serialized = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
