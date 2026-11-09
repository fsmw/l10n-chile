# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Locate independently numbered SET sections in an original SII TXT."""

import re
import unicodedata
from collections import Counter

from odoo import _
from odoo.exceptions import ValidationError

HEADING = re.compile(
    r"^\s*SET\s+.+?\s*-\s*NUMERO\s+DE\s+ATENCION\s*:\s*(\d+)\s*$", re.I
)
CASE = re.compile(r"^\s*CASO\s+(\d+)-(\d+)\s*$", re.I)
DOCUMENT = re.compile(r"^DOCUMENTO\s+(.+?)$", re.I)
DOCUMENT_TYPES = {
    "FACTURA ELECTRONICA": "33",
    "FACTURA NO AFECTA O EXENTA ELECTRONICA": "34",
    "GUIA DE DESPACHO": "52",
    "GUIA DE DESPACHO ELECTRONICA": "52",
    "NOTA DE DEBITO ELECTRONICA": "56",
    "NOTA DE CREDITO ELECTRONICA": "61",
}


def normalized_label(value):
    """Normalize accents and spacing for structural labels only."""
    plain = "".join(
        char
        for char in unicodedata.normalize("NFD", value.upper())
        if unicodedata.category(char) != "Mn"
    )
    return re.sub(r"\s+", " ", plain).strip()


def locate_sections(text):
    """Return section spans and type counts; never normalize fiscal details."""
    lines = text.splitlines()
    anchors = [
        (index, match.group(1))
        for index, line in enumerate(lines)
        if (match := HEADING.match(normalized_label(line)))
    ]
    if not anchors:
        raise ValidationError(_("The TXT has no numbered SII SET section."))
    attentions = [attention for _, attention in anchors]
    if len(set(attentions)) != len(attentions):
        raise ValidationError(_("The TXT repeats a SET attention number."))
    sections = []
    for ordinal, (start, attention) in enumerate(anchors):
        stop = anchors[ordinal + 1][0] if ordinal + 1 < len(anchors) else len(lines)
        case_anchors = []
        types = Counter()
        for index in range(start + 1, stop):
            label = normalized_label(lines[index])
            case = CASE.match(label)
            if case:
                if case.group(1) != attention:
                    raise ValidationError(_("A case has a different attention number."))
                case_anchors.append(index + 1)
            document = DOCUMENT.match(label)
            if document:
                types[DOCUMENT_TYPES.get(document.group(1), "unsupported")] += 1
        sections.append(
            {
                "attention_number": attention,
                "start_line": start + 1,
                "end_line": stop,
                "case_count": len(case_anchors),
                "document_counts": dict(sorted(types.items())),
            }
        )
    return sections
