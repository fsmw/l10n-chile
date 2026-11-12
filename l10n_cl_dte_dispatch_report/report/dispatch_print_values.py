# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.addons.l10n_cl_dte_core.tools.xml_safety import safe_fromstring

SII_NAMESPACE = "http://www.sii.cl/SiiDte"
NAMESPACES = {"s": SII_NAMESPACE}
TOTAL_TAGS = {
    "net": "MntNeto",
    "exempt": "MntExe",
    "vat": "IVA",
    "total": "MntTotal",
}
TRANSPORT_TAGS = {
    "vehicle_plate": "Patente",
    "carrier_vat": "RUTTrans",
    "destination_address": "DirDest",
}


def _text(element, path):
    """Return the stripped text at ``path`` or an empty string."""
    node = element.find(path, NAMESPACES)
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def _literal_ted(xml_bytes):
    """Return the TED exactly as signed in the document, or empty bytes."""
    start = xml_bytes.find(b"<TED")
    end_tag = b"</TED>"
    end = xml_bytes.find(end_tag)
    if start == -1 or end == -1:
        return b""
    return xml_bytes[start : end + len(end_tag)]


def _line_values(detail):
    return {
        "number": _text(detail, "s:NroLinDet"),
        "name": _text(detail, "s:NmbItem"),
        "quantity": _text(detail, "s:QtyItem"),
        "unit_price": _text(detail, "s:PrcItem"),
        "amount": _text(detail, "s:MontoItem"),
        "exempt_indicator": _text(detail, "s:IndExe"),
    }


def extract_guide_print_values(xml_bytes):
    """Return the values a guide printout needs, read from its signed XML.

    The TED is returned as its literal bytes, so the PDF417 can be encoded from
    the same bytes the SII signed. Missing optional values are empty.
    """
    root = safe_fromstring(xml_bytes)
    document = root.find(".//s:Documento", NAMESPACES)
    header = document.find("s:Encabezado", NAMESPACES)
    issuer = header.find("s:Emisor", NAMESPACES)
    receiver = header.find("s:Receptor", NAMESPACES)
    transport = receiver.find("s:Transporte", NAMESPACES)
    return {
        "document_id": document.get("ID", ""),
        "folio": _text(header, "s:IdDoc/s:Folio"),
        "issue_date": _text(header, "s:IdDoc/s:FchEmis"),
        "transfer_indicator": _text(header, "s:IdDoc/s:IndTraslado"),
        "issuer": {
            "vat": _text(issuer, "s:RUTEmisor"),
            "name": _text(issuer, "s:RznSoc"),
            "activity": _text(issuer, "s:GiroEmis"),
        },
        "receiver": {
            "vat": _text(receiver, "s:RUTRecep"),
            "name": _text(receiver, "s:RznSocRecep"),
            "address": _text(receiver, "s:DirRecep"),
        },
        "transport": (
            {key: _text(transport, f"s:{tag}") for key, tag in TRANSPORT_TAGS.items()}
            if transport is not None
            else {}
        ),
        "totals": {
            key: _text(header, f"s:Totales/s:{tag}") for key, tag in TOTAL_TAGS.items()
        },
        "lines": [
            _line_values(line) for line in document.findall("s:Detalle", NAMESPACES)
        ],
        "ted": _literal_ted(xml_bytes),
    }
