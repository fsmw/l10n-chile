# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Read the facts an exchange response repeats from the received XML."""

import re

from odoo.addons.l10n_cl_dte_inbox.services.incoming_parser import (
    SII_NS,
    extract_incoming_dtes,
)
from odoo.addons.l10n_cl_dte_signing.services.signature_service import DS_NAMESPACE

# Document types the SII exchange schemas accept (SiiTypes DTEType / DocType).
EXCHANGE_DOCUMENT_CODES = frozenset({"33", "34", "46", "52", "56", "61"})
NAMESPACES = {"sii": SII_NS, "ds": DS_NAMESPACE}


def format_rut(value):
    """Return a RUT as the schemas' ``digits-DV`` form, or an empty string."""
    compact = re.sub(r"[^0-9K]", "", (value or "").upper())
    if len(compact) < 2 or not compact[:-1].isdigit():
        return ""
    return f"{int(compact[:-1])}-{compact[-1]}"


def _text(node, path):
    values = node.xpath(path + "/text()", namespaces=NAMESPACES)
    return values[0].strip() if len(values) == 1 else ""


def dte_facts(node):
    """Return the identity, total and issuer email of one parsed DTE node."""
    header = "./sii:Documento/sii:Encabezado"
    total = _text(node, header + "/sii:Totales/sii:MntTotal")
    return {
        "document_code": _text(node, header + "/sii:IdDoc/sii:TipoDTE").lstrip("0"),
        "folio": _text(node, header + "/sii:IdDoc/sii:Folio"),
        "issue_date": _text(node, header + "/sii:IdDoc/sii:FchEmis"),
        "issuer_vat": format_rut(_text(node, header + "/sii:Emisor/sii:RUTEmisor")),
        "receiver_vat": format_rut(_text(node, header + "/sii:Receptor/sii:RUTRecep")),
        "total": total if total.isdigit() else "0",
        "issuer_email": _text(node, header + "/sii:Emisor/sii:CorreoEmisor"),
    }


def read_single_dte(content, translate):
    """Return the facts of the one DTE stored in an incoming document."""
    _envelope, nodes = extract_incoming_dtes(content, translate)
    return dte_facts(nodes[0])


def read_envelope(content, translate):
    """Return the envelope identity and the facts of each DTE, in file order."""
    envelope, nodes = extract_incoming_dtes(content, translate)
    is_envelope = envelope.tag != f"{{{SII_NS}}}DTE"
    if is_envelope:
        cover = "./sii:SetDTE/sii:Caratula"
        envelope_id = envelope.xpath("./sii:SetDTE/@ID", namespaces=NAMESPACES)
        issuer = format_rut(_text(envelope, cover + "/sii:RutEmisor"))
        receiver = format_rut(_text(envelope, cover + "/sii:RutReceptor"))
        digest = _text(
            envelope, "./ds:Signature/ds:SignedInfo/ds:Reference/ds:DigestValue"
        )
    else:
        facts = dte_facts(envelope)
        envelope_id = envelope.xpath("./sii:Documento/@ID", namespaces=NAMESPACES)
        issuer, receiver, digest = facts["issuer_vat"], facts["receiver_vat"], ""
    return {
        "envelope_id": (envelope_id[0] if envelope_id else "")[:80],
        "issuer_vat": issuer,
        "receiver_vat": receiver,
        "digest": "".join(digest.split()),
        "documents": [dte_facts(node) for node in nodes],
    }
