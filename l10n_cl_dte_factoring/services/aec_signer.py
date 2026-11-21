# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Sign and verify the three XMLDSig signatures of an AEC.

Each signature references one element by its ID. Digests and the SignedInfo
canonical form are computed in the element's final document context (the
inclusive C14N the SII verifies), and every signature is spliced into the
original bytes so the embedded DTE is never re-serialized.
"""

import base64
import binascii
import copy

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from lxml import etree

from odoo.exceptions import ValidationError
from odoo.tools.translate import LazyTranslate

from odoo.addons.l10n_cl_dte_signing.services.signature_service import (
    C14N_URI,
    RSA_SHA1_URI,
    SHA1_URI,
    SignatureService,
    _canonicalize,
    _parse_xml,
    _qname,
)

from .aec_composer import SIGNATURE_MARKERS, SII, marker_bytes

_lt = LazyTranslate(__name__, default_lang="en_US")

# (signature placeholder, path of the referenced element, path of its signature)
SIGNED_PARTS = (
    (
        SIGNATURE_MARKERS[0],
        "./sii:DocumentoAEC/sii:Cesiones/sii:DTECedido/sii:DocumentoDTECedido",
        "./sii:DocumentoAEC/sii:Cesiones/sii:DTECedido/ds:Signature",
    ),
    (
        SIGNATURE_MARKERS[1],
        "./sii:DocumentoAEC/sii:Cesiones/sii:Cesion/sii:DocumentoCesion",
        "./sii:DocumentoAEC/sii:Cesiones/sii:Cesion/ds:Signature",
    ),
    (SIGNATURE_MARKERS[2], "./sii:DocumentoAEC", "./ds:Signature"),
)
NAMESPACES = {"sii": SII, "ds": "http://www.w3.org/2000/09/xmldsig#"}


def _one(root, path):
    nodes = root.xpath(path, namespaces=NAMESPACES)
    if len(nodes) != 1:
        raise ValidationError(
            _lt("The AEC has a missing or ambiguous %(path)s.", path=path)
        )
    return nodes[0]


def _sha1(payload):
    digest = hashes.Hash(hashes.SHA1())
    digest.update(payload)
    return digest.finalize()


class AecSigner:
    """Sign an unsigned AEC with one company certificate."""

    def __init__(self, certificate, signature_service=None):
        self.certificate = certificate
        self.signature_service = signature_service or SignatureService()

    def sign(self, unsigned_bytes):
        """Return the signed AEC; inner signatures first, then the whole file."""
        signed = unsigned_bytes
        # The DTECedido and Cesion signatures are independent of each other;
        # the AEC signature covers both, so it must be computed last.
        for parts in (SIGNED_PARTS[:2], SIGNED_PARTS[2:]):
            root = _parse_xml(signed)
            replacements = [
                (marker, self._signature_bytes(root, marker, target_path))
                for marker, target_path, _signature_path in parts
            ]
            for marker, signature in replacements:
                placeholder = marker_bytes(marker)
                if signed.count(placeholder) != 1:
                    raise ValidationError(
                        _lt("The AEC signature placeholder is missing or repeated.")
                    )
                signed = signed.replace(placeholder, signature)
        verify_aec_signatures(signed, self.signature_service)
        return signed

    def _signature_bytes(self, root, marker, target_path):
        target = _one(root, target_path)
        target_id = target.get("ID")
        if not target_id or root.xpath("//*[@ID=$id]", id=target_id) != [target]:
            raise ValidationError(_lt("Each signed AEC element needs a unique ID."))
        placeholder = _one(root, f"//sii:{marker}")
        signature = etree.Element(_qname("Signature"), nsmap={None: NAMESPACES["ds"]})
        signed_info = etree.SubElement(signature, _qname("SignedInfo"))
        etree.SubElement(
            signed_info, _qname("CanonicalizationMethod"), Algorithm=C14N_URI
        )
        etree.SubElement(signed_info, _qname("SignatureMethod"), Algorithm=RSA_SHA1_URI)
        reference = etree.SubElement(
            signed_info, _qname("Reference"), URI=f"#{target_id}"
        )
        etree.SubElement(reference, _qname("DigestMethod"), Algorithm=SHA1_URI)
        etree.SubElement(reference, _qname("DigestValue")).text = base64.b64encode(
            _sha1(_canonicalize(target))
        ).decode("ascii")
        # SignedInfo inherits the namespaces in scope at the placeholder.
        in_context = copy.deepcopy(signature)
        placeholder.addnext(in_context)
        canonical_info = _canonicalize(in_context[0])
        in_context.getparent().remove(in_context)
        value = self.certificate._sign(
            canonical_info, hashing_algorithm="sha1", formatting="base64"
        )
        etree.SubElement(signature, _qname("SignatureValue")).text = value.decode(
            "ascii"
        )
        self.signature_service._add_key_info(signature, self.certificate)
        return etree.tostring(signature, encoding="ISO-8859-1", xml_declaration=False)


def _verify_part(service, root, target_path, signature_path):
    target = _one(root, target_path)
    signature = _one(root, signature_path)
    signed_info = service._signature_child(signature, "SignedInfo")
    reference = service._signature_child(signed_info, "Reference")
    target_id = target.get("ID")
    if (
        service._signature_child(signed_info, "CanonicalizationMethod").get("Algorithm")
        != C14N_URI
        or service._signature_child(signed_info, "SignatureMethod").get("Algorithm")
        != RSA_SHA1_URI
        or service._signature_child(reference, "DigestMethod").get("Algorithm")
        != SHA1_URI
    ):
        raise ValidationError(_lt("The AEC signature algorithms are unsupported."))
    if (
        not target_id
        or reference.get("URI") != f"#{target_id}"
        or root.xpath("//*[@ID=$id]", id=target_id) != [target]
    ):
        raise ValidationError(_lt("An AEC signature does not reference its element."))
    try:
        expected = base64.b64decode(
            "".join(
                (service._signature_child(reference, "DigestValue").text or "").split()
            ),
            validate=True,
        )
        value = base64.b64decode(
            "".join(
                (
                    service._signature_child(signature, "SignatureValue").text or ""
                ).split()
            ),
            validate=True,
        )
    except (binascii.Error, ValueError) as error:
        raise ValidationError(_lt("An AEC signature has invalid Base64.")) from error
    service._verify_document_digest(target, expected, allow_legacy=False)
    key = service._public_key_from_key_info(signature)
    if not isinstance(key, rsa.RSAPublicKey) or not service._verify_signed_info(
        key, value, signed_info, allow_legacy=False
    ):
        raise ValidationError(_lt("An AEC XML signature is invalid."))
    return target_id


def verify_aec_signatures(aec_bytes, signature_service=None):
    """Verify the AEC, DTECedido, Cesion and embedded DTE signatures."""
    service = signature_service or SignatureService()
    root = _parse_xml(aec_bytes)
    if root.tag != f"{{{SII}}}AEC":
        raise ValidationError(_lt("The XML root element must be AEC."))
    if len(root.xpath(".//ds:Signature", namespaces=NAMESPACES)) != 4:
        raise ValidationError(
            _lt("An AEC needs exactly the DTE, DTECedido, Cesion and AEC signatures.")
        )
    references = [
        _verify_part(service, root, target_path, signature_path)
        for _marker, target_path, signature_path in SIGNED_PARTS
    ]
    dte = _one(root, SIGNED_PARTS[0][1] + "/sii:DTE")
    service.verify_signature(
        etree.tostring(dte, encoding="ISO-8859-1"), sii_standalone=True
    )
    return {"valid": True, "references": references}
