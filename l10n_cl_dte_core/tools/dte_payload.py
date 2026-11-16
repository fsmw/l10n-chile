# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Select a single fiscal payload without changing its signed XML tree."""

from lxml import etree

SII_NAMESPACE = "http://www.sii.cl/SiiDte"
_FAMILIES = {
    "Documento": frozenset({"33", "34", "39", "41", "46", "52", "56", "61"}),
    "Exportaciones": frozenset({"110", "111", "112"}),
}


def _single_child(parent, name):
    children = parent.xpath("./*[local-name()=$name]", name=name)
    if len(children) != 1 or children[0].tag != f"{{{SII_NAMESPACE}}}{name}":
        raise ValueError("Expected exactly one SII header element.")
    return children[0]


def select_dte_payload(root, allowed_codes=None):
    """Return the original typed payload or raise ValueError on ambiguity.

    Callers must use the safe XML parser first. This structural check does not
    replace XSD, monetary, authorization or cryptographic validation. An empty
    allowlist rejects every code; None allows the families defined here.
    """
    if root.tag != f"{{{SII_NAMESPACE}}}DTE":
        raise ValueError("Expected an SII DTE root.")
    payloads = root.xpath(
        ".//*[local-name()='Documento' or local-name()='Exportaciones' "
        "or local-name()='Liquidacion' or local-name()='DTE']"
    )
    if len(payloads) != 1 or payloads[0].getparent() is not root:
        raise ValueError("Expected exactly one direct fiscal payload.")
    payload = payloads[0]
    name = etree.QName(payload).localname
    if name not in _FAMILIES or payload.tag != f"{{{SII_NAMESPACE}}}{name}":
        raise ValueError("Unsupported fiscal payload namespace or family.")
    identifiers = [node.get("ID") for node in root.iter() if node.get("ID") is not None]
    if not payload.get("ID") or any(not value.strip() for value in identifiers):
        raise ValueError("A fiscal payload needs a nonempty ID.")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("DTE IDs must be unique, including the root.")
    code_node = payload
    for part in ("Encabezado", "IdDoc", "TipoDTE"):
        code_node = _single_child(code_node, part)
    code = code_node.text
    if len(code_node) or code not in _FAMILIES[name]:
        raise ValueError("The DTE code does not match its payload family.")
    if allowed_codes is not None and code not in allowed_codes:
        raise ValueError("The DTE code is not allowed by this consumer.")
    return payload
