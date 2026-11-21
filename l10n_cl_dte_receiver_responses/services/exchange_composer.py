# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Compose the unsigned exchange responses a DTE receiver emails its supplier.

``RespuestaDTE`` (RespuestaEnvioDTE_v10.xsd) answers either the reception of
an envelope (RecepcionEnvio) or the commercial result of each DTE
(ResultadoDTE). ``EnvioRecibos`` (EnvioRecibos_v10.xsd) carries one Law 19.983
receipt of goods or services per DTE. Element order follows the schemas.
"""

from datetime import datetime, timezone

import pytz
from lxml import etree

from odoo.addons.l10n_cl_dte_inbox.services.incoming_parser import SII_NS

# Fixed value of DocumentoRecibo/Declaracion in Recibos_v10.xsd.
RECEIPT_DECLARATION = (
    "El acuse de recibo que se declara en este acto, de acuerdo a lo dispuesto "
    "en la letra b) del Art. 4, y la letra c) del Art. 5 de la Ley 19.983, "
    "acredita que la entrega de mercaderias o servicio(s) prestado(s) ha(n) "
    "sido recibido(s)."
)
# Texts the schema documents for each status code; the supplier reads them.
ENVELOPE_GLOSSES = {
    "0": "Envio Recibido Conforme",
    "1": "Envio Rechazado - Error de Schema",
    "2": "Envio Rechazado - Error de Firma",
    "3": "Envio Rechazado - RUT Receptor No Corresponde",
    "90": "Envio Rechazado - Archivo Repetido",
    "91": "Envio Rechazado - Archivo Ilegible",
    "99": "Envio Rechazado - Otros",
}
DOCUMENT_GLOSSES = {
    "0": "DTE Recibido OK",
    "1": "DTE No Recibido - Error de Firma",
    "2": "DTE No Recibido - Error en RUT Emisor",
    "3": "DTE No Recibido - Error en RUT Receptor",
    "4": "DTE No Recibido - DTE Repetido",
    "99": "DTE No Recibido - Otros",
}
RESULT_GLOSSES = {
    "0": "DTE Aceptado OK",
    "1": "DTE Aceptado con Discrepancias",
    "2": "DTE Rechazado",
}


def chile_timestamp(moment=None):
    """Return a naive UTC datetime (default: now) as Chilean local xs:dateTime."""
    moment = moment or datetime.now(timezone.utc).replace(tzinfo=None)
    local = pytz.utc.localize(moment).astimezone(pytz.timezone("America/Santiago"))
    return local.strftime("%Y-%m-%dT%H:%M:%S")


def _sub(parent, tag, text=None, **attrib):
    node = etree.SubElement(parent, f"{{{SII_NS}}}{tag}", **attrib)
    if text is not None:
        node.text = str(text)
    return node


def _identity(parent, facts, code_tag="TipoDTE"):
    _sub(parent, code_tag, facts["document_code"])
    _sub(parent, "Folio", facts["folio"])
    _sub(parent, "FchEmis", facts["issue_date"])
    _sub(parent, "RUTEmisor", facts["issuer_vat"])
    _sub(parent, "RUTRecep", facts["receiver_vat"])
    _sub(parent, "MntTotal", facts["total"])


def _root(tag):
    root = etree.Element(f"{{{SII_NS}}}{tag}", nsmap={None: SII_NS})
    root.set("version", "1.0")
    return root


def _answer_cover(result, cover, details):
    node = _sub(result, "Caratula", version="1.0")
    _sub(node, "RutResponde", cover["responder_vat"])
    _sub(node, "RutRecibe", cover["recipient_vat"])
    _sub(node, "IdRespuesta", cover["response_id"])
    _sub(node, "NroDetalles", details)
    _sub(node, "TmstFirmaResp", cover["timestamp"])


def compose_reception(cover, reception):
    """Return an unsigned RespuestaDTE with one RecepcionEnvio."""
    root = _root("RespuestaDTE")
    result = _sub(root, "Resultado", ID=f"RespuestaDTE-{cover['response_id']}")
    _answer_cover(result, cover, 1)
    node = _sub(result, "RecepcionEnvio")
    _sub(node, "NmbEnvio", reception["name"][:80])
    _sub(node, "FchRecep", reception["received_at"])
    _sub(node, "CodEnvio", reception["envelope_code"])
    _sub(node, "EnvioDTEID", reception["envelope_id"])
    if reception["digest"]:
        _sub(node, "Digest", reception["digest"])
    for tag, key in (("RutEmisor", "issuer_vat"), ("RutReceptor", "receiver_vat")):
        if reception[key]:
            _sub(node, tag, reception[key])
    _sub(node, "EstadoRecepEnv", reception["state"])
    _sub(node, "RecepEnvGlosa", reception["gloss"][:256])
    if reception["lines"]:
        _sub(node, "NroDTE", len(reception["lines"]))
    for line in reception["lines"]:
        detail = _sub(node, "RecepcionDTE")
        _identity(detail, line)
        _sub(detail, "EstadoRecepDTE", line["state"])
        _sub(detail, "RecepDTEGlosa", line["gloss"][:256])
    return root


def compose_result(cover, lines):
    """Return an unsigned RespuestaDTE with one ResultadoDTE per DTE."""
    root = _root("RespuestaDTE")
    result = _sub(root, "Resultado", ID=f"RespuestaDTE-{cover['response_id']}")
    _answer_cover(result, cover, len(lines))
    for line in lines:
        detail = _sub(result, "ResultadoDTE")
        _identity(detail, line)
        _sub(detail, "CodEnvio", line["envelope_code"])
        _sub(detail, "EstadoDTE", line["state"])
        _sub(detail, "EstadoDTEGlosa", line["gloss"][:256])
    return root


def compose_receipts(cover, receipts):
    """Return an unsigned EnvioRecibos; each Recibo is signed separately."""
    root = _root("EnvioRecibos")
    receipt_set = _sub(root, "SetRecibos", ID=f"SetRecibos-{cover['response_id']}")
    node = _sub(receipt_set, "Caratula", version="1.0")
    _sub(node, "RutResponde", cover["responder_vat"])
    _sub(node, "RutRecibe", cover["recipient_vat"])
    _sub(node, "TmstFirmaEnv", cover["timestamp"])
    for receipt in receipts:
        wrapper = _sub(receipt_set, "Recibo", version="1.0")
        document = _sub(wrapper, "DocumentoRecibo", ID=receipt["id"])
        _identity(document, receipt, code_tag="TipoDoc")
        _sub(document, "Recinto", receipt["site"][:80])
        _sub(document, "RutFirma", receipt["signer_vat"])
        _sub(document, "Declaracion", RECEIPT_DECLARATION)
        _sub(document, "TmstFirmaRecibo", cover["timestamp"])
    return root
