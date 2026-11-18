# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import re
from decimal import ROUND_HALF_UP, Decimal

from lxml import etree

from odoo.tools import email_normalize

from odoo.addons.l10n_cl_dte_core.tools.xml_safety import safe_fromstring

_DECLARATION = b'<?xml version="1.0" encoding="ISO-8859-1" ?>'
_INDENT = "    "
_QUOTES = re.compile(r"""(["'])""")
_DTE_UOM_LABELS = {
    "uom.product_uom_unit": "U",
    "uom.product_uom_dozen": "DOC",
    "uom.product_uom_meter": "MT",
    "uom.product_uom_foot": "P2",
    "uom.product_uom_kgm": "KN",
    "uom.product_uom_litre": "LT",
    "uom.product_uom_gram": "GN",
}


def format_uom(external_id, name):
    return _DTE_UOM_LABELS.get(external_id, (name or "")[:4])


def fixed_decimal(value, digits):
    quantum = Decimal(1).scaleb(-digits)
    return format(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP), "f")


def format_email(value):
    email = email_normalize(value or "")
    return email if email and len(email) <= 80 else None


def _encode_text_quotes(root):
    # Keep numeric references in the wire XML while preserving parsed text.
    for node in list(root.iter()):
        fragments = _QUOTES.split(node.text or "")
        if len(fragments) == 1:
            continue
        node.text = fragments[0]
        for index, (quote, following_text) in enumerate(
            zip(fragments[1::2], fragments[2::2], strict=True)
        ):
            entity = etree.Entity(f"#{ord(quote)}")
            entity.tail = following_text
            node.insert(index, entity)


def serialize_dd(dd_xml):
    """Indent the TED data without changing the order of the CAF fields."""
    dd = safe_fromstring(dd_xml, encoding="ISO-8859-1")
    etree.indent(dd, space=_INDENT, level=3)
    _encode_text_quotes(dd)
    return etree.tostring(
        dd, encoding="ISO-8859-1", xml_declaration=False, with_tail=False
    )


def serialize_unsigned_dte(root, document, ted_xml, *, root_indent):
    """Capture the document wire layout before XMLDSig computes its digest."""
    etree.indent(document, space=_INDENT, level=2)
    document[-1].tail = "\n" + _INDENT * 3
    root.text = "\n" + _INDENT * 3
    document.tail = "\n" + _INDENT * root_indent + "\n"
    _encode_text_quotes(root)
    payload = etree.tostring(root, encoding="ISO-8859-1", xml_declaration=False)
    if ted_xml:
        marker = b"<TED_PLACEHOLDER/>\n" + b" " * 12
        if payload.count(marker) != 1:
            raise ValueError("The DTE TED placeholder is missing or ambiguous.")
        payload = payload.replace(marker, ted_xml, 1)
    return _DECLARATION + b"\n" + (_INDENT * root_indent).encode("ascii") + payload
