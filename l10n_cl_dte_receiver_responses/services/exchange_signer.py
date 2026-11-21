# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Sign, verify and schema-validate the emailed exchange responses.

Each XMLDSig references one element by its ID with the inclusive C14N the SII
uses: RespuestaDTE signs Resultado; EnvioRecibos signs every DocumentoRecibo
inside its Recibo first and then SetRecibos, whose digest covers them.
"""

import base64
import binascii
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from lxml import etree

from odoo.exceptions import ValidationError
from odoo.tools.translate import LazyTranslate

from odoo.addons.l10n_cl_dte_core.tools.xml_safety import safe_xml_schema
from odoo.addons.l10n_cl_dte_signing.services.signature_service import (
    C14N_URI,
    DS_NAMESPACE,
    RSA_SHA1_URI,
    SHA1_URI,
    SignatureService,
    _canonicalize,
    _parse_xml,
    _qname,
)

_lt = LazyTranslate(__name__, default_lang="en_US")
XSD_DIR = Path(__file__).resolve().parent.parent / "static" / "xsd"
SCHEMAS = {
    "RespuestaDTE": "RespuestaEnvioDTE_v10.xsd",
    "EnvioRecibos": "EnvioRecibos_v10.xsd",
}


def _sha1(payload):
    digest = hashes.Hash(hashes.SHA1())
    digest.update(payload)
    return digest.finalize()


def _children(node, name):
    return node.xpath("./*[local-name()=$name]", name=name)


def _one(node, name):
    found = _children(node, name)
    if len(found) != 1:
        raise ValidationError(
            _lt("The exchange response needs exactly one %(name)s.", name=name)
        )
    return found[0]


class ExchangeSigner:
    """Append XMLDSig signatures made with one company certificate."""

    def __init__(self, certificate, signature_service=None):
        self.certificate = certificate
        self.service = signature_service or SignatureService()

    def sign(self, root):
        """Sign a composed response tree and return its ISO-8859-1 bytes."""
        kind = etree.QName(root).localname
        if kind == "RespuestaDTE":
            self._sign_child(root, _one(root, "Resultado"))
        elif kind == "EnvioRecibos":
            receipt_set = _one(root, "SetRecibos")
            for receipt in _children(receipt_set, "Recibo"):
                self._sign_child(receipt, _one(receipt, "DocumentoRecibo"))
            self._sign_child(root, receipt_set)
        else:
            raise ValidationError(_lt("Use a RespuestaDTE or EnvioRecibos root."))
        signed = etree.tostring(root, encoding="ISO-8859-1", xml_declaration=True)
        validate_exchange_schema(signed)
        verify_exchange_signatures(signed, self.service)
        return signed

    def _sign_child(self, parent, target):
        target_id = target.get("ID")
        if not target_id or target.getroottree().xpath(
            "//*[@ID=$id]", id=target_id
        ) != [target]:
            raise ValidationError(_lt("Each signed element needs a unique ID."))
        signature = etree.SubElement(
            parent, _qname("Signature"), nsmap={None: DS_NAMESPACE}
        )
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
        value = self.certificate._sign(
            _canonicalize(signed_info), hashing_algorithm="sha1", formatting="base64"
        )
        etree.SubElement(signature, _qname("SignatureValue")).text = value.decode(
            "ascii"
        )
        self.service._add_key_info(signature, self.certificate)
        return signature


@lru_cache(maxsize=2)
def _schema(filename):
    return safe_xml_schema(str(XSD_DIR / filename))


def validate_exchange_schema(content):
    """Validate a response against its official SII schema."""
    root = _parse_xml(content)
    filename = SCHEMAS.get(etree.QName(root).localname)
    if not filename:
        raise ValidationError(_lt("Use a RespuestaDTE or EnvioRecibos root."))
    schema = _schema(filename)
    if not schema.validate(root):
        error = schema.error_log.last_error
        raise ValidationError(
            _lt(
                "The exchange response does not match %(schema)s: %(error)s",
                schema=filename,
                error=error.message if error else "",
            )
        )
    return root


def _b64(node):
    return base64.b64decode("".join((node.text or "").split()), validate=True)


def _signature_values(service, signature, target):
    signed_info = service._signature_child(signature, "SignedInfo")
    reference = service._signature_child(signed_info, "Reference")
    algorithms = (
        service._signature_child(signed_info, "CanonicalizationMethod").get(
            "Algorithm"
        ),
        service._signature_child(signed_info, "SignatureMethod").get("Algorithm"),
        service._signature_child(reference, "DigestMethod").get("Algorithm"),
    )
    if algorithms != (C14N_URI, RSA_SHA1_URI, SHA1_URI):
        raise ValidationError(_lt("The exchange signature algorithms are unsupported."))
    target_id = target.get("ID")
    if (
        not target_id
        or reference.get("URI") != f"#{target_id}"
        or target.getroottree().xpath("//*[@ID=$id]", id=target_id) != [target]
    ):
        raise ValidationError(
            _lt("The exchange signature does not reference its element.")
        )
    try:
        digest = _b64(service._signature_child(reference, "DigestValue"))
        value = _b64(service._signature_child(signature, "SignatureValue"))
    except (binascii.Error, ValueError) as error:
        raise ValidationError(
            _lt("The exchange signature has invalid Base64.")
        ) from error
    return signed_info, digest, value


def _verify_child(service, parent, target_name):
    target = _one(parent, target_name)
    signature = _one(parent, "Signature")
    if signature.tag != _qname("Signature"):
        raise ValidationError(_lt("The exchange signature namespace is invalid."))
    signed_info, digest, value = _signature_values(service, signature, target)
    service._verify_document_digest(target, digest, allow_legacy=False)
    key = service._public_key_from_key_info(signature)
    if not isinstance(key, rsa.RSAPublicKey) or not service._verify_signed_info(
        key, value, signed_info, allow_legacy=False
    ):
        raise ValidationError(_lt("The exchange XML signature is invalid."))
    return target


def verify_exchange_signatures(content, signature_service=None):
    """Verify every signature of a RespuestaDTE or EnvioRecibos."""
    service = signature_service or SignatureService()
    root = _parse_xml(content)
    kind = etree.QName(root).localname
    if kind == "RespuestaDTE":
        _verify_child(service, root, "Resultado")
        return 1
    if kind != "EnvioRecibos":
        raise ValidationError(_lt("Use a RespuestaDTE or EnvioRecibos root."))
    receipt_set = _verify_child(service, root, "SetRecibos")
    receipts = _children(receipt_set, "Recibo")
    for receipt in receipts:
        _verify_child(service, receipt, "DocumentoRecibo")
    return len(receipts) + 1
