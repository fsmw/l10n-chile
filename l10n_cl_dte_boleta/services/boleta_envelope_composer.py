# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Wrap one signed boleta in the EnvioBOLETA envelope of the SII REST service."""

import uuid
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from lxml import etree

from odoo.exceptions import ValidationError
from odoo.tools.translate import LazyTranslate

from odoo.addons.l10n_cl_dte_core.tools.xml_safety import (
    safe_fromstring,
    safe_xml_schema,
)
from odoo.addons.l10n_cl_dte_gateway.services.envelope_composer import (
    SII_VAT,
    SiiEnvelopeComposer,
)
from odoo.addons.l10n_cl_dte_signing.services.certificate_identity import normalize_rut
from odoo.addons.l10n_cl_dte_signing.services.signature_service import (
    SignatureService,
    _without_xml_declaration,
)

_lt = LazyTranslate(__name__, default_lang="en_US")
SII = "http://www.sii.cl/SiiDte"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
BOLETA_CODES = frozenset({"39", "41"})


@lru_cache(maxsize=1)
def _schema():
    # The official EnvioBOLETA_v11.xsd has a facet libxml refuses; the
    # bundled compat copy only fixes that, as the inbox validation does.
    path = (
        Path(__file__).resolve().parent.parent / "static/xsd/EnvioBOLETA_v11_compat.xsd"
    )
    return safe_xml_schema(str(path))


def _tag(name):
    return f"{{{SII}}}{name}"


def _add(parent, name, value):
    etree.SubElement(parent, _tag(name)).text = str(value)


class BoletaEnvelopeComposer:
    """Compose, sign and validate the EnvioBOLETA of one boleta."""

    def compose(self, company, signed_boleta, environment):
        signature_service = SignatureService()
        sender = SiiEnvelopeComposer().validate_sender_identity(
            company, company.dte_sii_sender_vat, signature_service, environment
        )
        dte = safe_fromstring(signed_boleta)
        code = dte.findtext(
            f"{_tag('Documento')}/{_tag('Encabezado')}/"
            f"{_tag('IdDoc')}/{_tag('TipoDTE')}"
        )
        issuer = dte.findtext(
            f"{_tag('Documento')}/{_tag('Encabezado')}/"
            f"{_tag('Emisor')}/{_tag('RUTEmisor')}"
        )
        if code not in BOLETA_CODES:
            raise ValidationError(_lt("An EnvioBOLETA carries boletas 39 or 41."))
        if normalize_rut(issuer) != normalize_rut(company.vat):
            raise ValidationError(_lt("The boleta issuer differs from the company."))
        root = etree.Element(
            _tag("EnvioBOLETA"), nsmap={None: SII, "xsi": XSI}, version="1.0"
        )
        root.set(f"{{{XSI}}}schemaLocation", f"{SII} EnvioBOLETA_v11.xsd")
        set_dte = etree.SubElement(
            root, _tag("SetDTE"), ID="SetDoc-" + uuid.uuid4().hex
        )
        cover = etree.SubElement(set_dte, _tag("Caratula"), version="1.0")
        _add(cover, "RutEmisor", normalize_rut(issuer))
        _add(cover, "RutEnvia", sender)
        _add(cover, "RutReceptor", SII_VAT)
        _add(cover, "FchResol", company.dte_sii_resolution_date.isoformat())
        _add(cover, "NroResol", company.dte_sii_resolution_number)
        _add(
            cover,
            "TmstFirmaEnv",
            datetime.now(ZoneInfo("America/Santiago")).strftime("%Y-%m-%dT%H:%M:%S"),
        )
        subtotal = etree.SubElement(cover, _tag("SubTotDTE"))
        _add(subtotal, "TpoDTE", code)
        _add(subtotal, "NroDTE", 1)
        unsigned = etree.tostring(
            root, encoding="ISO-8859-1", xml_declaration=True
        ).replace(
            b"</SetDTE>", _without_xml_declaration(signed_boleta) + b"</SetDTE>", 1
        )
        envelope = signature_service.sign_envelope(company, environment, unsigned)
        try:
            _schema().assertValid(safe_fromstring(envelope))
        except etree.DocumentInvalid as error:
            raise ValidationError(
                _lt(
                    "The EnvioBOLETA does not comply with the SII schema: %(error)s",
                    error=error.error_log.last_error,
                )
            ) from error
        signature_service.verify_envelope_signature(envelope)
        return envelope
