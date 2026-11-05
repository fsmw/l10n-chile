# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import re

from lxml import etree

from odoo import _
from odoo.exceptions import ValidationError
from odoo.tools.misc import file_path

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import (
    SII_NAMESPACE,
    _extract_dd_bytes,
)

BOLETA_DOCUMENT_CODES = frozenset({"39", "41"})


class BoletaXmlComposer:
    """Compose and validate type-39/41 XML without using invoice serializers."""

    def compose_unsigned_document(self, snapshot):
        """Serialize one boleta snapshot to the SII boleta document format."""
        document_code = str(snapshot["document_code"])
        if document_code not in BOLETA_DOCUMENT_CODES:
            raise ValidationError(_("Only boletas 39 and 41 can use this serializer."))
        root = etree.Element(
            f"{{{SII_NAMESPACE}}}DTE", nsmap={None: SII_NAMESPACE}, version="1.0"
        )
        document = etree.SubElement(
            root,
            f"{{{SII_NAMESPACE}}}Documento",
            ID=f"F{int(snapshot['folio']):010d}T{document_code}",
        )
        header = etree.SubElement(document, f"{{{SII_NAMESPACE}}}Encabezado")
        identification = etree.SubElement(header, f"{{{SII_NAMESPACE}}}IdDoc")
        _add_text(identification, "TipoDTE", document_code)
        _add_text(identification, "Folio", snapshot["folio"])
        _add_text(identification, "FchEmis", snapshot["issue_date"])
        _add_text(identification, "IndServicio", snapshot["service_indicator"])
        _add_optional_text(
            identification, "IndMntNeto", snapshot.get("net_amount_indicator")
        )
        _add_optional_text(identification, "MedioPago", snapshot.get("payment_method"))
        issuer_data = snapshot["issuer"]
        issuer = etree.SubElement(header, f"{{{SII_NAMESPACE}}}Emisor")
        for field, value in (
            ("RUTEmisor", issuer_data["vat"]),
            ("RznSocEmisor", issuer_data["name"]),
            ("GiroEmisor", issuer_data["activity"]),
        ):
            _add_text(issuer, field, value)
        for key, xml_name in (
            ("address", "DirOrigen"),
            ("commune", "CmnaOrigen"),
            ("city", "CiudadOrigen"),
        ):
            _add_optional_text(issuer, xml_name, issuer_data.get(key))

        receiver_data = snapshot["receiver"]
        receiver = etree.SubElement(header, f"{{{SII_NAMESPACE}}}Receptor")
        _add_text(receiver, "RUTRecep", receiver_data["vat"])
        _add_optional_text(receiver, "CdgIntRecep", receiver_data.get("internal_code"))
        _add_optional_text(receiver, "RznSocRecep", receiver_data.get("name"))
        _add_optional_text(receiver, "CorreoRecep", receiver_data.get("email"))
        _add_optional_text(receiver, "TelefonoRecep", receiver_data.get("phone"))
        for key, xml_name in (
            ("address", "DirRecep"),
            ("commune", "CmnaRecep"),
            ("city", "CiudadRecep"),
        ):
            _add_optional_text(receiver, xml_name, receiver_data.get(key))

        totals_data = snapshot["totals"]
        totals = etree.SubElement(header, f"{{{SII_NAMESPACE}}}Totales")
        _add_optional_text(totals, "MntNeto", totals_data.get("net"))
        _add_optional_text(totals, "MntExe", totals_data.get("exempt"))
        _add_optional_text(totals, "IVA", totals_data.get("vat"))
        _add_text(totals, "MntTotal", totals_data["total"])

        for index, line_data in enumerate(snapshot["lines"], start=1):
            detail = etree.SubElement(document, f"{{{SII_NAMESPACE}}}Detalle")
            _add_text(detail, "NroLinDet", index)
            if line_data.get("exempt"):
                _add_text(detail, "IndExe", "1")
            _add_text(detail, "NmbItem", line_data["name"])
            _add_optional_text(detail, "DscItem", line_data.get("description"))
            _add_text(detail, "QtyItem", line_data["quantity"])
            _add_optional_text(detail, "UnmdItem", line_data.get("uom"))
            _add_text(detail, "PrcItem", line_data["unit_price"])
            if line_data.get("discount_percent"):
                _add_text(detail, "DescuentoPct", line_data["discount_percent"])
            _add_text(detail, "MontoItem", line_data["subtotal"])
        etree.SubElement(document, f"{{{SII_NAMESPACE}}}TED_PLACEHOLDER")
        _add_text(document, "TmstFirma", snapshot["document_timestamp"])
        try:
            xml_bytes = etree.tostring(
                root, encoding="ISO-8859-1", xml_declaration=True, pretty_print=True
            )
        except (LookupError, UnicodeEncodeError) as error:
            raise ValidationError(
                _("Boleta text must be representable in ISO-8859-1.")
            ) from error
        placeholder = b"<TED_PLACEHOLDER/>"
        if xml_bytes.count(placeholder) != 1:
            raise ValidationError(_("The boleta TED placeholder is invalid."))
        ted_xml = snapshot.get("ted_xml")
        if not ted_xml:
            raise ValidationError(_("The boleta must include its signed TED."))
        return xml_bytes.replace(placeholder, ted_xml, 1)

    def compose_ted(self, snapshot, caf_authorization):
        """Build the boleta TED and FRMT using the shared signing primitives."""
        document_code = str(snapshot["document_code"])
        if document_code not in BOLETA_DOCUMENT_CODES:
            raise ValidationError(_("Only boletas 39 and 41 can use a TED."))
        caf_authorization.ensure_one()
        if caf_authorization.document_code != document_code:
            raise ValidationError(_("The CAF type must match the boleta type."))
        folio = int(snapshot["folio"])
        if not caf_authorization.folio_start <= folio <= caf_authorization.folio_end:
            raise ValidationError(_("The folio is outside the CAF range."))
        if caf_authorization.company_id.id != snapshot["company_id"]:
            raise ValidationError(_("The snapshot and CAF companies do not match."))
        caf_authorization._check_caf_integrity()
        caf_content = caf_authorization._decode_content(caf_authorization.caf_file)
        caf_element = _extract_caf(caf_content)
        issuer = snapshot["issuer"]
        receiver = snapshot["receiver"]
        first_line = snapshot["lines"][0]
        dd = etree.Element("DD")
        for name, value in (
            ("RE", issuer["vat"]),
            ("TD", document_code),
            ("F", folio),
            ("FE", snapshot["issue_date"]),
            ("RR", receiver["vat"]),
            ("RSR", receiver["name"]),
            ("MNT", snapshot["totals"]["total"]),
            ("IT1", first_line.get("description") or first_line["name"]),
        ):
            _add_plain_text(dd, name, value)
        dd.append(caf_element)
        _add_plain_text(dd, "TSTED", snapshot["ted_timestamp"])
        dd_bytes = etree.tostring(dd, encoding="ISO-8859-1", xml_declaration=False)
        frmt = SignatureService().sign_ted(
            caf_authorization.company_id, caf_authorization, dd_bytes
        )
        wrapped_frmt = (
            base64.encodebytes(base64.b64decode(frmt)).decode("ascii").strip()
        )
        return (
            b'<TED version="1.0">'
            + dd_bytes
            + b'<FRMT algoritmo="SHA1withRSA">'
            + wrapped_frmt.encode("ascii")
            + b"</FRMT></TED>"
        )

    def validate_document(self, xml_bytes):
        """Validate boleta semantics, official boleta XSD, and signatures."""
        schema = _boleta_schema()
        try:
            root = etree.fromstring(xml_bytes, parser=_safe_xml_parser())
            schema.assertValid(root)
        except etree.DocumentInvalid as error:
            raise ValidationError(
                _("The boleta does not comply with the official SII schema: %s")
                % schema.error_log.last_error
            ) from error
        except etree.XMLSyntaxError as error:
            raise ValidationError(
                _("The boleta XML is malformed: %s") % error
            ) from error
        document = root.find(f"{{{SII_NAMESPACE}}}Documento")
        if document is None:
            raise ValidationError(_("The XML does not contain a DTE document."))
        id_doc = f"{{{SII_NAMESPACE}}}Encabezado/{{{SII_NAMESPACE}}}IdDoc"
        code = document.findtext(f"{id_doc}/{{{SII_NAMESPACE}}}TipoDTE")
        indicator = document.findtext(f"{id_doc}/{{{SII_NAMESPACE}}}IndServicio")
        if code not in BOLETA_DOCUMENT_CODES or indicator != "3":
            raise ValidationError(
                _("The boleta must use type 39/41 and service indicator 3.")
            )
        if code == "41" and document.find(f".//{{{SII_NAMESPACE}}}IndExe") is None:
            raise ValidationError(_("Boleta 41 must contain exempt detail lines."))
        ted = document.find(f"{{{SII_NAMESPACE}}}TED")
        dd = ted.find(f"{{{SII_NAMESPACE}}}DD") if ted is not None else None
        caf = dd.find(f"{{{SII_NAMESPACE}}}CAF") if dd is not None else None
        frmt = ted.findtext(f"{{{SII_NAMESPACE}}}FRMT") if ted is not None else None
        if dd is None or caf is None or not frmt:
            raise ValidationError(_("The boleta TED is missing DD, CAF, or FRMT."))
        signature_service = SignatureService()
        signature_service.verify_ted(_extract_dd_bytes(xml_bytes), frmt, caf)
        signature_service.verify_signature(xml_bytes)
        return True


def _add_text(parent, name, value):
    if value is None:
        raise ValidationError(_("The boleta field %s is required.") % name)
    element = etree.SubElement(parent, f"{{{SII_NAMESPACE}}}{name}")
    element.text = str(value)
    return element


def _add_optional_text(parent, name, value):
    if value not in (None, False, ""):
        return _add_text(parent, name, value)
    return None


def _add_plain_text(parent, name, value):
    if value is None:
        raise ValidationError(_("The boleta TED field %s is required.") % name)
    element = etree.SubElement(parent, name)
    element.text = str(value)
    return element


def _extract_caf(caf_bytes):
    match = re.search(rb"<CAF(?:\s[^>]*)?>.*?</CAF>", caf_bytes, flags=re.DOTALL)
    if not match:
        raise ValidationError(_("The validated CAF does not contain a CAF element."))
    try:
        return etree.fromstring(match.group(0), parser=_safe_xml_parser())
    except etree.XMLSyntaxError as error:
        raise ValidationError(_("The CAF XML is malformed.")) from error


def _safe_xml_parser():
    return etree.XMLParser(
        resolve_entities=False, no_network=True, remove_blank_text=False
    )


def _boleta_schema():
    try:
        schema_path = file_path("l10n_cl_dte_boleta/static/xsd/BoletaDTE_v11.xsd")
    except FileNotFoundError as error:
        raise ValidationError(
            _("The official boleta schema is not available.")
        ) from error
    try:
        return etree.XMLSchema(etree.parse(schema_path, parser=_safe_xml_parser()))
    except (OSError, etree.XMLSchemaParseError, etree.XMLSyntaxError) as error:
        raise ValidationError(
            _("The official boleta schema cannot be loaded.")
        ) from error
