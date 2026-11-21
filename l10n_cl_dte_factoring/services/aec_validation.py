# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Validate an AEC against the official SII schema and its business rules."""

from functools import lru_cache
from pathlib import Path

from lxml import etree

from odoo.exceptions import ValidationError
from odoo.tools.translate import LazyTranslate

from odoo.addons.l10n_cl_dte_core.tools.xml_safety import safe_xml_schema
from odoo.addons.l10n_cl_dte_signing.services.signature_service import _parse_xml

from .aec_composer import (
    ASSIGNABLE_DOCUMENT_CODES,
    SII,
    dte_element_bytes,
    read_dte_identity,
)

_lt = LazyTranslate(__name__, default_lang="en_US")
_NS = {"sii": SII, "ds": "http://www.w3.org/2000/09/xmldsig#"}
_CESION = "./sii:DocumentoAEC/sii:Cesiones/sii:Cesion"
_DOCUMENT = _CESION + "/sii:DocumentoCesion"


@lru_cache(maxsize=1)
def _schema():
    path = Path(__file__).resolve().parent.parent / "static/xsd/AEC_v10.xsd"
    return safe_xml_schema(str(path))


def validate_aec_schema(aec_bytes):
    """Validate the AEC with the SII's AEC_v10.xsd and its included schemas."""
    root = _parse_xml(aec_bytes)
    schema = _schema()
    if not schema.validate(root):
        error = schema.error_log.last_error
        raise ValidationError(
            _lt(
                "The AEC does not match the SII schema: %(error)s",
                error=error.message if error else "",
            )
        )
    return root


def _text(root, path):
    values = root.xpath(path + "/text()", namespaces=_NS)
    if len(values) != 1:
        raise ValidationError(
            _lt("The AEC has a missing or ambiguous %(path)s.", path=path)
        )
    return values[0].strip()


def check_aec_structure(aec_bytes, dte_bytes):
    """Check the rules the schema cannot express, against the assigned DTE."""
    root = _parse_xml(aec_bytes)
    if root.xpath("count(" + _CESION + ")", namespaces=_NS) != 1:
        raise ValidationError(_lt("This AEC must contain exactly one assignment."))
    identity = read_dte_identity(dte_bytes)
    for name in ("TipoDTE", "RUTEmisor", "RUTReceptor", "Folio", "FchEmis", "MntTotal"):
        if _text(root, f"{_DOCUMENT}/sii:IdDTE/sii:{name}") != identity[name]:
            raise ValidationError(
                _lt("The AEC %(field)s differs from the assigned DTE.", field=name)
            )
    if identity["TipoDTE"] not in ASSIGNABLE_DOCUMENT_CODES:
        raise ValidationError(_lt("Only DTE 33 and 34 can be assigned."))
    cover = "./sii:DocumentoAEC/sii:Caratula"
    if _text(root, f"{cover}/sii:RutCedente") != _text(
        root, f"{_DOCUMENT}/sii:Cedente/sii:RUT"
    ) or _text(root, f"{cover}/sii:RutCesionario") != _text(
        root, f"{_DOCUMENT}/sii:Cesionario/sii:RUT"
    ):
        raise ValidationError(_lt("The AEC cover and assignment parties differ."))
    if _text(root, f"{_DOCUMENT}/sii:SeqCesion") != "1":
        raise ValidationError(_lt("Only a first assignment can be issued."))
    if int(_text(root, f"{_DOCUMENT}/sii:MontoCesion")) > int(identity["MntTotal"]):
        raise ValidationError(_lt("The assigned amount exceeds the DTE total."))
    signers = root.xpath(f"{_DOCUMENT}/sii:Cedente/sii:RUTAutorizado", namespaces=_NS)
    signatures = root.xpath(f"{_CESION}/ds:Signature", namespaces=_NS)
    if not signers or len(signers) != len(signatures):
        raise ValidationError(
            _lt("Each authorized assignor signer needs one Cesion signature.")
        )
    embedded = root.xpath(
        "./sii:DocumentoAEC/sii:Cesiones/sii:DTECedido/sii:DocumentoDTECedido/sii:DTE",
        namespaces=_NS,
    )
    if len(embedded) != 1 or etree.QName(embedded[0]).localname != "DTE":
        raise ValidationError(_lt("The AEC must embed exactly one DTE."))
    return True


def check_embedded_bytes(aec_bytes, dte_bytes):
    """Prove the AEC carries the stored signed DTE byte for byte."""
    if aec_bytes.count(dte_element_bytes(dte_bytes)) != 1:
        raise ValidationError(_lt("The AEC does not embed the exact signed DTE bytes."))
    return True
