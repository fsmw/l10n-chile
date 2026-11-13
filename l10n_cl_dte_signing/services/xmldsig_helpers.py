# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""XML parsing, canonicalization and RSA key helpers for XMLDSig signing."""

import copy
import re

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from lxml import etree

from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_core.tools.xml_safety import safe_fromstring

DS_NAMESPACE = "http://www.w3.org/2000/09/xmldsig#"


def _parse_xml(xml_bytes):
    if isinstance(xml_bytes, str):
        xml_bytes = xml_bytes.encode("ISO-8859-1")
    if not isinstance(xml_bytes, bytes) or b"<!DOCTYPE" in xml_bytes.upper():
        raise ValidationError(_("The XML input must be bytes without a DTD."))
    has_declaration = xml_bytes.lstrip().startswith(b"<?xml")
    try:
        return safe_fromstring(
            xml_bytes, encoding=None if has_declaration else "ISO-8859-1"
        )
    except (etree.XMLSyntaxError, ValueError) as error:
        raise ValidationError(
            _("The XML input is not well-formed: %s") % error
        ) from error


def _without_xml_declaration(xml_bytes):
    return re.sub(rb"^\s*<\?xml[^?]*\?>\s*", b"", xml_bytes, count=1).lstrip()


def _canonicalize(element, exclusive=False):
    try:
        # Direct C14N of a descendant can reset inherited default namespaces
        # in nested nodes. Materialize every in-scope namespace at the subtree
        # apex before canonicalizing, matching standalone XMLDSig processors.
        standalone = safe_fromstring(
            etree.tostring(element, encoding="UTF-8", with_tail=False)
        )
        if not exclusive:
            # Inclusive C14N 1.0 also inherits xml:* attributes from omitted
            # ancestors. Preserve the nearest value without replacing local ones.
            for ancestor in element.iterancestors():
                for name, value in ancestor.attrib.items():
                    if (
                        name.startswith("{http://www.w3.org/XML/1998/namespace}")
                        and name not in standalone.attrib
                    ):
                        standalone.set(name, value)
        return etree.tostring(
            standalone, method="c14n", exclusive=exclusive, with_comments=False
        )
    except (etree.C14NError, etree.XMLSyntaxError, ValueError) as error:
        raise ValidationError(_("The XML cannot be canonicalized.")) from error


def _canonicalize_without_sii_namespace(element):
    unqualified = copy.deepcopy(element)
    for node in unqualified.iter():
        qualified_name = etree.QName(node)
        if qualified_name.namespace == "http://www.sii.cl/SiiDte":
            node.tag = qualified_name.localname
    return _canonicalize(unqualified, exclusive=True)


def _sii_signing_bytes(element):
    if isinstance(element, bytes):
        serialized = element
    else:
        serialized = etree.tostring(element, encoding="ISO-8859-1", with_tail=False)
    return re.sub(rb"\n\s*", b"", serialized)


def _load_rsa_private_key(pem):
    if not pem:
        raise ValidationError(_("The CAF does not contain an RSA private key."))
    try:
        key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
    except (UnicodeEncodeError, TypeError, ValueError) as error:
        raise ValidationError(_("The CAF private key is not valid PEM.")) from error
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValidationError(_("The CAF private key must be RSA."))
    return key


def _load_rsa_public_key(pem):
    if not pem:
        raise ValidationError(_("The CAF does not contain an RSA public key."))
    try:
        key = serialization.load_pem_public_key(pem.encode("ascii"))
    except (UnicodeEncodeError, TypeError, ValueError) as error:
        raise ValidationError(_("The CAF public key is not valid PEM.")) from error
    if not isinstance(key, rsa.RSAPublicKey):
        raise ValidationError(_("The CAF public key must be RSA."))
    return key


def _local_name(element):
    return etree.QName(element).localname


def _local_path_text(element, path):
    current = element
    for name in path:
        matches = [child for child in current if _local_name(child) == name]
        if len(matches) != 1:
            return None
        current = matches[0]
    return current.text


def _qname(local_name):
    return f"{{{DS_NAMESPACE}}}{local_name}"
