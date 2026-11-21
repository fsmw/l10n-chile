# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Parse and verify the responses a receiver emails back for our DTEs.

``RespuestaDTE`` carries either the reception of an envelope (RecepcionEnvio)
or the commercial result of each DTE (ResultadoDTE), signed over Resultado.
``EnvioRecibos`` carries the Law 19.983 receipts of goods, signed over
SetRecibos and, for each Recibo, over its DocumentoRecibo. Every signature must
verify; nothing is trusted otherwise.
"""

import base64
import binascii

from cryptography.hazmat.primitives.asymmetric import rsa
from lxml import etree

from odoo.exceptions import ValidationError
from odoo.tools.translate import LazyTranslate

from odoo.addons.l10n_cl_dte_core.tools.xml_safety import safe_fromstring
from odoo.addons.l10n_cl_dte_signing.services.signature_service import (
    C14N_URI,
    DS_NAMESPACE,
    RSA_SHA1_URI,
    SHA1_URI,
)

_lt = LazyTranslate(__name__, default_lang="en_US")
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_ITEMS = 2000
RESPONSE_ROOTS = frozenset({"RespuestaDTE", "EnvioRecibos"})
ENVELOPED_URI = DS_NAMESPACE + "enveloped-signature"
SUPPORTED_TRANSFORMS = frozenset(
    {(), (C14N_URI,), (ENVELOPED_URI,), (ENVELOPED_URI, C14N_URI)}
)
RESULT_CODES = {
    "0": "RESULT_ACCEPTED",
    "1": "RESULT_OBJECTIONS",
    "2": "RESULT_REJECTED",
}


def _children(node, name):
    return node.xpath("./*[local-name()=$name]", name=name)


def _one(node, name):
    found = _children(node, name)
    if len(found) != 1:
        raise ValidationError(
            _lt("The receiver response needs exactly one %(name)s.", name=name)
        )
    return found[0]


def _text(node, name):
    found = _children(node, name)
    return (found[0].text or "").strip() if len(found) == 1 else ""


def read_response_root(content):
    """Return the parsed root of a bounded XML without DTD or entities."""
    if not isinstance(content, bytes) or not content:
        raise ValidationError(_lt("The receiver response XML is empty."))
    if len(content) > MAX_RESPONSE_BYTES:
        raise ValidationError(_lt("The receiver response XML exceeds 5 MiB."))
    try:
        root = safe_fromstring(content)
    except (etree.XMLSyntaxError, ValueError) as error:
        raise ValidationError(
            _lt("The receiver response is not well-formed XML.")
        ) from error
    if root.getroottree().docinfo.doctype:
        raise ValidationError(_lt("DTD declarations are not allowed."))
    return root


def is_response_xml(content):
    try:
        return etree.QName(read_response_root(content)).localname in RESPONSE_ROOTS
    except ValidationError:
        return False


def _signature_parts(signature, target):
    ds = {"ds": DS_NAMESPACE}
    signed_info = signature.find(f"{{{DS_NAMESPACE}}}SignedInfo")
    value = signature.findtext(f"{{{DS_NAMESPACE}}}SignatureValue")
    if signed_info is None or not value:
        raise ValidationError(_lt("The receiver response signature is incomplete."))
    references = signed_info.findall(f"{{{DS_NAMESPACE}}}Reference")
    methods = (
        signed_info.xpath("./ds:CanonicalizationMethod/@Algorithm", namespaces=ds),
        signed_info.xpath("./ds:SignatureMethod/@Algorithm", namespaces=ds),
    )
    target_id = target.get("ID")
    if (
        methods != ([C14N_URI], [RSA_SHA1_URI])
        or len(references) != 1
        or not target_id
        or references[0].get("URI") != f"#{target_id}"
        or target.getroottree().xpath("//*[@ID=$id]", id=target_id) != [target]
        or references[0].xpath("./ds:DigestMethod/@Algorithm", namespaces=ds)
        != [SHA1_URI]
        or tuple(
            references[0].xpath(
                "./ds:Transforms/ds:Transform/@Algorithm", namespaces=ds
            )
        )
        not in SUPPORTED_TRANSFORMS
    ):
        raise ValidationError(
            _lt("The receiver response signature does not reference its content.")
        )
    try:
        digest = base64.b64decode(
            "".join(
                (references[0].findtext(f"{{{DS_NAMESPACE}}}DigestValue") or "").split()
            ),
            validate=True,
        )
        signed = base64.b64decode("".join(value.split()), validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValidationError(
            _lt("The receiver response signature has invalid Base64.")
        ) from error
    return signed_info, digest, signed


def verify_signed_child(signer, parent, target_name):
    """Verify the XMLDSig of ``parent`` over its unique ``target_name`` child."""
    target = _one(parent, target_name)
    signatures = parent.findall(f"{{{DS_NAMESPACE}}}Signature")
    if len(signatures) != 1:
        raise ValidationError(_lt("The receiver response needs one XML signature."))
    signed_info, digest, signed = _signature_parts(signatures[0], target)
    signer._verify_document_digest(target, digest)
    key = signer._public_key_from_key_info(signatures[0])
    if not isinstance(key, rsa.RSAPublicKey) or not signer._verify_signed_info(
        key, signed, signed_info
    ):
        raise ValidationError(_lt("The receiver response XML signature is invalid."))
    return target


def _reference(node, code_tag, code, description, date_key, actor):
    return {
        "issuer_vat": _text(node, "RUTEmisor"),
        "receiver_vat": _text(node, "RUTRecep"),
        "document_code": _text(node, code_tag).lstrip("0"),
        "folio": _text(node, "Folio"),
        "code": code,
        "description": description[:1000],
        "date_key": date_key,
        "actor_vat": actor,
    }


def _parse_answer(root, signer):
    result = verify_signed_child(signer, root, "Resultado")
    cover = _one(result, "Caratula")
    responder = _text(cover, "RutResponde")
    stamp = _text(cover, "TmstFirmaResp")
    items = []
    for reception in _children(result, "RecepcionEnvio"):
        for line in _children(reception, "RecepcionDTE"):
            state = _text(line, "EstadoRecepDTE")
            items.append(
                _reference(
                    line,
                    "TipoDTE",
                    "RECEPTION_OK" if state == "0" else "RECEPTION_ERROR",
                    _text(line, "RecepDTEGlosa") or state,
                    stamp,
                    responder,
                )
            )
    for line in _children(result, "ResultadoDTE"):
        state = _text(line, "EstadoDTE")
        items.append(
            _reference(
                line,
                "TipoDTE",
                RESULT_CODES.get(state, "RESULT_UNKNOWN"),
                _text(line, "EstadoDTEGlosa") or state,
                stamp,
                responder,
            )
        )
    return "respuesta_dte", cover, stamp, items


def _parse_receipts(root, signer):
    receipts = verify_signed_child(signer, root, "SetRecibos")
    cover = _one(receipts, "Caratula")
    items = []
    for receipt in _children(receipts, "Recibo"):
        document = verify_signed_child(signer, receipt, "DocumentoRecibo")
        items.append(
            _reference(
                document,
                "TipoDoc",
                "GOODS_RECEIPT",
                _text(document, "Declaracion") or _text(document, "Recinto"),
                _text(document, "TmstFirmaRecibo"),
                _text(document, "RutFirma") or _text(cover, "RutResponde"),
            )
        )
    return "envio_recibos", cover, _text(cover, "TmstFirmaEnv"), items


def parse_receiver_response(content, signer):
    """Return the verified identity and referenced DTEs of a receiver response."""
    root = read_response_root(content)
    kind = etree.QName(root).localname
    if kind == "RespuestaDTE":
        kind, cover, stamp, items = _parse_answer(root, signer)
    elif kind == "EnvioRecibos":
        kind, cover, stamp, items = _parse_receipts(root, signer)
    else:
        raise ValidationError(_lt("Use a RespuestaDTE or EnvioRecibos root."))
    if len(items) > MAX_ITEMS:
        raise ValidationError(_lt("A receiver response cannot exceed 2000 DTEs."))
    return {
        "kind": kind,
        "responder_vat": _text(cover, "RutResponde"),
        "recipient_vat": _text(cover, "RutRecibe"),
        "signed_at": stamp,
        "items": items,
    }
