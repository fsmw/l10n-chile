# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Compose and verify an offline SII EnvioDTE from signed DTE evidence."""

import re
from collections import Counter
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from lxml import etree

from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_signing.services.signature_service import (
    SignatureService,
)
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import DteXmlComposer

SII = "http://www.sii.cl/SiiDte"
XSI = "http://www.w3.org/2001/XMLSchema-instance"


def _tag(name):
    return f"{{{SII}}}{name}"


def _add(parent, name, value):
    if value in (None, False, ""):
        raise ValidationError(_("The envelope header field %s is required.") % name)
    etree.SubElement(parent, _tag(name)).text = str(value)


def _parse(data):
    if not isinstance(data, bytes) or b"<!DOCTYPE" in data.upper():
        raise ValidationError(_("Envelope XML must be bytes without a DTD."))
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    try:
        return etree.fromstring(data, parser=parser)
    except (ValueError, etree.XMLSyntaxError) as error:
        raise ValidationError(
            _("The envelope XML is not well formed: %s") % error
        ) from error


@lru_cache(maxsize=1)
def _schema():
    path = Path(__file__).resolve().parent.parent / "static/xsd/EnvioDTE_v10.xsd"
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    return etree.XMLSchema(etree.parse(str(path), parser))


class SiiEnvelopeComposer:
    def compose(self, company, signed_documents, header, signature_service=None):
        if company.dte_sii_environment != "certification":
            raise ValidationError(
                _("Offline certification envelopes require Certification mode.")
            )
        if (
            not isinstance(signed_documents, list)
            or not 0 < len(signed_documents) <= 2000
        ):
            raise ValidationError(_("An EnvioDTE needs between 1 and 2000 documents."))
        if not isinstance(header, dict) or set(header) != {
            "id",
            "sender_vat",
            "resolution_number",
            "resolution_date",
        }:
            raise ValidationError(_("The certification envelope header is incomplete."))
        signature_service = signature_service or SignatureService()
        dtes = []
        identifiers = set()
        type_counts = Counter()
        issuer_vat = None
        for content in signed_documents:
            DteXmlComposer().validate_document(content)
            dte = _parse(content)
            if dte.tag != _tag("DTE"):
                raise ValidationError(_("The envelope contains a non-DTE element."))
            document = dte.find(_tag("Documento"))
            identifier = document.get("ID") if document is not None else None
            if (
                not identifier
                or identifier in identifiers
                or identifier == header["id"]
            ):
                raise ValidationError(_("DTE and SetDTE identifiers must be unique."))
            identifiers.add(identifier)
            id_doc = document.find("{}/{}".format(_tag("Encabezado"), _tag("IdDoc")))
            emitter = document.find("{}/{}".format(_tag("Encabezado"), _tag("Emisor")))
            if id_doc is None or emitter is None:
                raise ValidationError(_("A signed DTE lacks fiscal identification."))
            code = id_doc.findtext(_tag("TipoDTE"))
            current_issuer = emitter.findtext(_tag("RUTEmisor"))
            if issuer_vat is None:
                issuer_vat = current_issuer
            elif issuer_vat != current_issuer:
                raise ValidationError(_("All DTE in an envelope need the same issuer."))
            type_counts[code] += 1
            dtes.append(dte)
        issuer_normalized = re.sub(r"[^0-9K]", "", (issuer_vat or "").upper())
        company_normalized = re.sub(
            r"[^0-9K]", "", (company.partner_id.vat or "").upper()
        )
        if not issuer_normalized or issuer_normalized != company_normalized:
            raise ValidationError(
                _("The DTE issuer differs from the envelope company.")
            )
        root = etree.Element(
            _tag("EnvioDTE"),
            nsmap={None: SII, "xsi": XSI},
            version="1.0",
        )
        root.set(f"{{{XSI}}}schemaLocation", f"{SII} EnvioDTE_v10.xsd")
        set_dte = etree.SubElement(root, _tag("SetDTE"), ID=header["id"])
        cover = etree.SubElement(set_dte, _tag("Caratula"), version="1.0")
        _add(cover, "RutEmisor", issuer_vat)
        _add(cover, "RutEnvia", header["sender_vat"])
        _add(cover, "RutReceptor", "60803000-K")
        resolution_date = header["resolution_date"]
        _add(
            cover,
            "FchResol",
            resolution_date.isoformat()
            if hasattr(resolution_date, "isoformat")
            else resolution_date,
        )
        _add(cover, "NroResol", header["resolution_number"])
        _add(
            cover,
            "TmstFirmaEnv",
            datetime.now(ZoneInfo("America/Santiago")).strftime("%Y-%m-%dT%H:%M:%S"),
        )
        for code, count in sorted(type_counts.items()):
            subtotal = etree.SubElement(cover, _tag("SubTotDTE"))
            _add(subtotal, "TpoDTE", code)
            _add(subtotal, "NroDTE", count)
        for dte in dtes:
            set_dte.append(dte)
        unsigned = etree.tostring(
            root, encoding="ISO-8859-1", xml_declaration=True, pretty_print=False
        )
        envelope = signature_service.sign_envelope(company, "certification", unsigned)
        self.validate(
            envelope, expected_count=len(dtes), signature_service=signature_service
        )
        return envelope

    def validate(self, envelope_bytes, expected_count=None, signature_service=None):
        root = _parse(envelope_bytes)
        if root.tag != _tag("EnvioDTE"):
            raise ValidationError(_("The XML root must be EnvioDTE."))
        try:
            _schema().assertValid(root)
        except etree.DocumentInvalid as error:
            raise ValidationError(
                _("The envelope does not comply with SII EnvioDTE v10: %s")
                % error.error_log.last_error
            ) from error
        signature_service = signature_service or SignatureService()
        signature_service.verify_envelope_signature(envelope_bytes)
        set_dte = root.find(_tag("SetDTE"))
        dtes = set_dte.findall(_tag("DTE")) if set_dte is not None else []
        if expected_count is not None and len(dtes) != expected_count:
            raise ValidationError(
                _("The envelope document count differs from the input.")
            )
        ids = [element.get("ID") for element in root.xpath(".//*[@ID]")]
        if not all(ids) or len(ids) != len(set(ids)):
            raise ValidationError(_("The envelope has duplicate XML identifiers."))
        for dte in dtes:
            DteXmlComposer().validate_document(
                etree.tostring(dte, encoding="ISO-8859-1", xml_declaration=True)
            )
        cover = set_dte.find(_tag("Caratula"))
        expected_counts = Counter()
        expected_issuer = None
        for dte in dtes:
            header = dte.find("{}/{}".format(_tag("Documento"), _tag("Encabezado")))
            if header is None:
                raise ValidationError(_("An envelope DTE is missing its header."))
            code = header.findtext("{}/{}".format(_tag("IdDoc"), _tag("TipoDTE")))
            issuer = header.findtext("{}/{}".format(_tag("Emisor"), _tag("RUTEmisor")))
            if expected_issuer is None:
                expected_issuer = issuer
            if issuer != expected_issuer:
                raise ValidationError(_("An envelope contains multiple issuers."))
            expected_counts[code] += 1
        declared_counts = {}
        for subtotal in cover.findall(_tag("SubTotDTE")):
            code = subtotal.findtext(_tag("TpoDTE"))
            if code in declared_counts:
                raise ValidationError(_("The envelope repeats a DTE subtotal type."))
            declared_counts[code] = int(subtotal.findtext(_tag("NroDTE")))
        if (
            dict(expected_counts) != declared_counts
            or cover.findtext(_tag("RutEmisor")) != expected_issuer
        ):
            raise ValidationError(_("The envelope cover does not match its DTE."))
        return True
