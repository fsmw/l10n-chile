# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Extract versioned section and case structure from an original SII SET TXT."""

import hashlib
import re
import unicodedata
from collections import Counter

from odoo.exceptions import ValidationError

PARSER_VERSION = "sii_set_structure_v1"
MAX_SOURCE_BYTES = 2 * 1024 * 1024
HEADING = re.compile(
    r"^\s*SET\s+.+?\s*-\s*NUMERO\s+DE\s+ATENCION\s*:\s*(\d+)\s*$", re.I
)
CASE = re.compile(r"^\s*CASO\s+(\d+)-(\d+)\s*$", re.I)
DOCUMENT = re.compile(r"^\s*DOCUMENTO\s+(.+?)\s*$", re.I)
DOCUMENT_TYPES = {
    "FACTURA ELECTRONICA": "33",
    "FACTURA NO AFECTA O EXENTA ELECTRONICA": "34",
    "GUIA DE DESPACHO": "52",
    "GUIA DE DESPACHO ELECTRONICA": "52",
    "NOTA DE DEBITO ELECTRONICA": "56",
    "NOTA DE CREDITO ELECTRONICA": "61",
}


def _label(value):
    plain = "".join(
        char
        for char in unicodedata.normalize("NFD", value.upper())
        if unicodedata.category(char) != "Mn"
    )
    return re.sub(r"\s+", " ", plain).strip()


class CertificationSetStructureParser:
    """Identify SET sections and ordered cases without inferring fiscal values."""

    def __init__(self, env):
        self.env = env

    def parse(self, source_bytes):
        if (
            not isinstance(source_bytes, bytes)
            or not 0 < len(source_bytes) <= MAX_SOURCE_BYTES
            or b"\x00" in source_bytes
            or source_bytes.startswith((b"PK\x03\x04", b"%PDF"))
        ):
            raise ValidationError(
                self.env._("The source must be a non-empty TXT below 2 MiB.")
            )
        encoding = self._encoding(source_bytes)
        text = source_bytes.decode("utf-8-sig" if encoding == "utf-8" else encoding)
        if any(
            ord(char) < 32 and char not in "\t\n\r\f" for char in text
        ) or text.lstrip().startswith(("<", "{", "[")):
            raise ValidationError(
                self.env._("The source does not look like a plain TXT file.")
            )

        lines = text.splitlines()
        anchors = [
            (index, match.group(1))
            for index, line in enumerate(lines)
            if (match := HEADING.match(_label(line)))
        ]
        if not anchors:
            raise ValidationError(
                self.env._("The TXT has no numbered SII SET section.")
            )
        attention_numbers = [attention for _, attention in anchors]
        if len(set(attention_numbers)) != len(attention_numbers):
            raise ValidationError(self.env._("The TXT repeats a SET attention number."))

        sections = []
        for position, (start, attention) in enumerate(anchors):
            stop = (
                anchors[position + 1][0] if position + 1 < len(anchors) else len(lines)
            )
            sections.append(self._parse_section(lines, start, stop, attention))
        return {
            "parser_version": PARSER_VERSION,
            "source_digest": hashlib.sha256(source_bytes).hexdigest(),
            "encoding": encoding,
            "sections": sections,
            "diagnostics": [
                diagnostic
                for section in sections
                for diagnostic in section["diagnostics"]
            ],
        }

    @staticmethod
    def _encoding(source_bytes):
        try:
            source_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            return "iso-8859-1"
        return "utf-8"

    def _parse_section(self, lines, start, stop, attention):
        cases = []
        counts = Counter()
        identifiers = set()
        diagnostics = []
        for index in range(start + 1, stop):
            case_match = CASE.match(_label(lines[index]))
            if not case_match:
                continue
            case_attention, case_number = case_match.groups()
            if case_attention != attention:
                raise ValidationError(
                    self.env._("A case has a different attention number.")
                )
            identifier = f"{case_attention}-{case_number}"
            if identifier in identifiers:
                raise ValidationError(self.env._("The TXT repeats a case identifier."))
            identifiers.add(identifier)
            end = next(
                (
                    candidate
                    for candidate in range(index + 1, stop)
                    if CASE.match(_label(lines[candidate]))
                ),
                stop,
            )
            documents = [
                match.group(1)
                for line in lines[index + 1 : end]
                if (match := DOCUMENT.match(_label(line)))
            ]
            if len(documents) != 1:
                raise ValidationError(
                    self.env._("Each case must contain exactly one DOCUMENTO line.")
                )
            document_code = DOCUMENT_TYPES.get(documents[0], "unsupported")
            counts[document_code] += 1
            if document_code == "unsupported":
                diagnostics.append(
                    {
                        "code": "unsupported_document_type",
                        "case": identifier,
                        "source_line": index + 2,
                        "source_text": lines[index + 1],
                    }
                )
            cases.append(
                {
                    "identifier": identifier,
                    "sequence": len(cases) + 1,
                    "document_code": document_code,
                    "source_start_line": index + 1,
                    "source_end_line": end,
                    "instructions": "\n".join(lines[index:end]),
                }
            )
        if not cases:
            raise ValidationError(self.env._("The SET section contains no cases."))
        return {
            "attention_number": attention,
            "source_start_line": start + 1,
            "source_end_line": stop,
            "case_count": len(cases),
            "document_counts": dict(sorted(counts.items())),
            "cases": cases,
            "diagnostics": diagnostics,
        }
