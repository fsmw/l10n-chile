# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib

from lxml import etree

DSIG_NS = "http://www.w3.org/2000/09/xmldsig#"
C14N_URI = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
RSA_SHA1_URI = f"{DSIG_NS}rsa-sha1"
SHA1_URI = f"{DSIG_NS}sha1"


def canonical_xml(node):
    return etree.tostring(node, method="c14n", exclusive=False, with_comments=False)


def add_xml_signature(
    root,
    *,
    reference_id,
    sign,
    modulus,
    exponent,
    certificate_der,
    namespace_map=None,
):
    """Add an enveloped RSA-SHA1 signature over the element identified by ID."""
    signed_node = next(
        (node for node in root.iter() if node.get("ID") == reference_id), None
    )
    if signed_node is None:
        raise ValueError(f"XML signature target #{reference_id} does not exist")

    ds = f"{{{DSIG_NS}}}"
    signed_info = etree.Element(
        ds + "SignedInfo", nsmap=namespace_map or {None: DSIG_NS}
    )
    etree.SubElement(
        signed_info,
        ds + "CanonicalizationMethod",
        Algorithm=C14N_URI,
    )
    etree.SubElement(signed_info, ds + "SignatureMethod", Algorithm=RSA_SHA1_URI)
    reference = etree.SubElement(signed_info, ds + "Reference", URI=f"#{reference_id}")
    transforms = etree.SubElement(reference, ds + "Transforms")
    etree.SubElement(transforms, ds + "Transform", Algorithm=C14N_URI)
    etree.SubElement(reference, ds + "DigestMethod", Algorithm=SHA1_URI)
    digest = hashlib.sha1(canonical_xml(signed_node)).digest()
    etree.SubElement(reference, ds + "DigestValue").text = base64.b64encode(
        digest
    ).decode("ascii")

    signature = etree.Element(ds + "Signature", nsmap={None: DSIG_NS})
    signature.append(signed_info)
    signed_node.addnext(signature)
    signature_value = sign(canonical_xml(signed_info))
    if isinstance(signature_value, bytes):
        signature_value = signature_value.decode("ascii")
    etree.SubElement(signature, ds + "SignatureValue").text = signature_value
    key_info = etree.SubElement(signature, ds + "KeyInfo")
    key_value = etree.SubElement(key_info, ds + "KeyValue")
    rsa_key_value = etree.SubElement(key_value, ds + "RSAKeyValue")
    etree.SubElement(rsa_key_value, ds + "Modulus").text = _b64_text(modulus)
    etree.SubElement(rsa_key_value, ds + "Exponent").text = _b64_text(exponent)
    x509_data = etree.SubElement(key_info, ds + "X509Data")
    etree.SubElement(x509_data, ds + "X509Certificate").text = _b64_text(
        certificate_der
    )
    return signature


def sign_with_certificate(root, reference_id, certificate, namespace_map=None):
    """Sign XML using the private key linked to an Odoo certificate record."""
    modulus, exponent = certificate._get_public_key_numbers_bytes(formatting="base64")
    return add_xml_signature(
        root,
        reference_id=reference_id,
        sign=lambda data: certificate._sign(
            data, hashing_algorithm="sha1", formatting="base64"
        ),
        modulus=modulus,
        exponent=exponent,
        certificate_der=certificate._get_der_certificate_bytes(formatting="base64"),
        namespace_map=namespace_map,
    )


def _b64_text(value):
    if isinstance(value, bytes):
        value = value.decode("ascii")
    return "".join(value.split())
