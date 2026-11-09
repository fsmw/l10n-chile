# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import html
import re
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path

from lxml import etree

from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_signing.services.signature_service import (
    SignatureService,
)

from .ted_composer import DteTedComposer

SII_NAMESPACE = "http://www.sii.cl/SiiDte"


class DteXmlComposer:
    """Compose and validate supported electronic invoices and boletas."""

    @staticmethod
    def line_discount_amount(quantity, unit_price, discount_percent):
        return (
            Decimal(quantity) * Decimal(unit_price) * Decimal(discount_percent) / 100
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    def compose_unsigned_document(self, snapshot):
        """Serialize a previously prepared DTE snapshot.

        `snapshot` is a plain mapping produced by InvoiceSnapshotService. Fiscal
        arithmetic belongs to account.move; this method only serializes values.
        """
        _require_supported_snapshot(snapshot)
        root = etree.Element(
            f"{{{SII_NAMESPACE}}}DTE",
            nsmap={None: SII_NAMESPACE},
            version="1.0",
        )
        document_code = str(snapshot["document_code"])
        document_id = f"F{int(snapshot['folio']):010d}T{document_code}"
        document = etree.SubElement(
            root, f"{{{SII_NAMESPACE}}}Documento", ID=document_id
        )
        header = etree.SubElement(document, f"{{{SII_NAMESPACE}}}Encabezado")
        identification = etree.SubElement(header, f"{{{SII_NAMESPACE}}}IdDoc")
        _add_text(identification, "TipoDTE", document_code)
        _add_text(identification, "Folio", snapshot["folio"])
        _add_text(identification, "FchEmis", snapshot["issue_date"])

        issuer_data = snapshot["issuer"]
        issuer = etree.SubElement(header, f"{{{SII_NAMESPACE}}}Emisor")
        _add_text(issuer, "RUTEmisor", issuer_data["vat"])
        _add_text(issuer, "RznSoc", issuer_data["name"])
        _add_text(issuer, "GiroEmis", issuer_data["activity"])
        _add_text(issuer, "Acteco", issuer_data["activity_code"])
        for field_name, xml_name in (
            ("address", "DirOrigen"),
            ("commune", "CmnaOrigen"),
            ("city", "CiudadOrigen"),
        ):
            _add_optional_text(issuer, xml_name, issuer_data.get(field_name))

        receiver_data = snapshot["receiver"]
        receiver = etree.SubElement(header, f"{{{SII_NAMESPACE}}}Receptor")
        _add_text(receiver, "RUTRecep", receiver_data["vat"])
        _add_text(receiver, "RznSocRecep", receiver_data["name"])
        for field_name, xml_name in (
            ("activity", "GiroRecep"),
            ("address", "DirRecep"),
            ("commune", "CmnaRecep"),
            ("city", "CiudadRecep"),
        ):
            _add_optional_text(receiver, xml_name, receiver_data.get(field_name))

        totals_data = snapshot["totals"]
        totals = etree.SubElement(header, f"{{{SII_NAMESPACE}}}Totales")
        _add_optional_text(totals, "MntNeto", totals_data.get("net"))
        _add_optional_text(totals, "MntExe", totals_data.get("exempt"))
        _add_optional_text(totals, "TasaIVA", totals_data.get("vat_rate"))
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
                discount = self.line_discount_amount(
                    line_data["quantity"],
                    line_data["unit_price"],
                    line_data["discount_percent"],
                )
                _add_text(detail, "DescuentoMonto", str(discount))
            _add_text(detail, "MontoItem", line_data["subtotal"])

        global_discount = snapshot.get("global_discount")
        if global_discount:
            if (
                set(global_discount)
                != {
                    "movement",
                    "description",
                    "value_type",
                    "value",
                }
                or global_discount["movement"] != "D"
            ):
                raise ValidationError(_("The global DTE discount is invalid."))
            discount_node = etree.SubElement(
                document, f"{{{SII_NAMESPACE}}}DscRcgGlobal"
            )
            _add_text(discount_node, "NroLinDR", 1)
            _add_text(discount_node, "TpoMov", global_discount["movement"])
            _add_optional_text(discount_node, "GlosaDR", global_discount["description"])
            _add_text(discount_node, "TpoValor", global_discount["value_type"])
            _add_text(discount_node, "ValorDR", global_discount["value"])

        references = snapshot.get("references", [])
        if not isinstance(references, list) or len(references) > 40:
            raise ValidationError(_("A DTE supports at most 40 references."))
        for index, reference_data in enumerate(references, start=1):
            reference_code = _validate_reference_data(reference_data, index)
            reference = etree.SubElement(document, f"{{{SII_NAMESPACE}}}Referencia")
            _add_text(reference, "NroLinRef", index)
            _add_text(reference, "TpoDocRef", reference_data["document_type"])
            _add_text(reference, "FolioRef", reference_data["folio"])
            _add_text(reference, "FchRef", reference_data["date"])
            _add_optional_text(reference, "CodRef", reference_code)
            _add_text(reference, "RazonRef", reference_data["reason"])

        ted_xml = snapshot.get("ted_xml")
        if ted_xml:
            etree.SubElement(document, f"{{{SII_NAMESPACE}}}TED_PLACEHOLDER")
        _add_text(document, "TmstFirma", snapshot["document_timestamp"])
        try:
            xml_bytes = etree.tostring(
                root,
                encoding="ISO-8859-1",
                xml_declaration=True,
                pretty_print=True,
            )
        except (LookupError, UnicodeEncodeError) as error:
            raise ValidationError(
                _("The invoice contains characters outside ISO-8859-1.")
            ) from error
        if ted_xml:
            placeholder = b"<TED_PLACEHOLDER/>"
            if xml_bytes.count(placeholder) != 1:
                raise ValidationError(
                    _("The serialized DTE TED placeholder is invalid.")
                )
            xml_bytes = xml_bytes.replace(placeholder, ted_xml)
        return xml_bytes

    def compose_ted(self, snapshot, caf_authorization, signature_service=None):
        """Build TED from the snapshot and return it with its FRMT signature."""
        _require_supported_snapshot(snapshot)
        return DteTedComposer().compose_ted(
            snapshot, caf_authorization, signature_service
        )

    def validate_document(self, xml_bytes, signature_service=None, *, strict_sii=False):
        """Check SII XSD, TED FRMT, and the DTE XMLDSig before dispatch."""
        root = _parse_document(xml_bytes)
        if root.tag != f"{{{SII_NAMESPACE}}}DTE":
            raise ValidationError(_("The XML root element must be a SII DTE."))
        try:
            _dte_schema().assertValid(root)
        except etree.DocumentInvalid as error:
            raise ValidationError(
                _("The DTE does not comply with the bundled SII DTE v10 schema: %s")
                % error.error_log.last_error
            ) from error

        signature_service = signature_service or SignatureService()
        ted = root.find(f"{{{SII_NAMESPACE}}}Documento/{{{SII_NAMESPACE}}}TED")
        if ted is None:
            raise ValidationError(_("The DTE does not contain a TED."))
        dd = _find_local_child(ted, "DD")
        caf = _find_local_child(dd, "CAF") if dd is not None else None
        frmt = _find_local_text(ted, "FRMT")
        if dd is None or caf is None or not frmt:
            raise ValidationError(_("The TED is missing DD, CAF, or FRMT."))
        signature_service.verify_ted(_extract_dd_bytes(xml_bytes), frmt, caf)
        if strict_sii:
            self._validate_line_discounts(root)
            self._validate_exempt_total(root)
            signature_service.verify_signature(xml_bytes, sii_standalone=True)
        else:
            signature_service.verify_signature(xml_bytes)
        return True

    @staticmethod
    def line_discount_amount(quantity, unit_price, discount_percent):
        return (
            Decimal(quantity) * Decimal(unit_price) * Decimal(discount_percent) / 100
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    def _validate_line_discounts(self, root):
        prefix = f"{{{SII_NAMESPACE}}}"
        for detail in root.findall(prefix + "Documento/" + prefix + "Detalle"):
            percent = detail.findtext(prefix + "DescuentoPct")
            if percent is None:
                continue
            amount = detail.findtext(prefix + "DescuentoMonto")
            expected = self.line_discount_amount(
                detail.findtext(prefix + "QtyItem"),
                detail.findtext(prefix + "PrcItem"),
                percent,
            )
            if amount is None or Decimal(amount) != expected:
                raise ValidationError(
                    _("The DTE detail discount amount is missing or inconsistent.")
                )

    @staticmethod
    def _validate_exempt_total(root):
        prefix = f"{{{SII_NAMESPACE}}}"
        document = root.find(prefix + "Documento")
        expected = sum(
            (
                Decimal(detail.findtext(prefix + "MontoItem"))
                for detail in document.findall(prefix + "Detalle")
                if detail.findtext(prefix + "IndExe") == "1"
            ),
            Decimal(0),
        )
        for adjustment in document.findall(prefix + "DscRcgGlobal"):
            if adjustment.findtext(prefix + "IndExeDR") != "1":
                continue
            amount = Decimal(adjustment.findtext(prefix + "ValorDR"))
            if adjustment.findtext(prefix + "TpoValor") == "%":
                amount = expected * amount / 100
            amount = amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            expected += (
                amount if adjustment.findtext(prefix + "TpoMov") == "R" else -amount
            )
        actual = Decimal(
            document.findtext(
                prefix + "Encabezado/" + prefix + "Totales/" + prefix + "MntExe"
            )
            or "0"
        )
        if actual != expected:
            raise ValidationError(
                _(
                    "The DTE exempt total differs from its detail "
                    "and exempt adjustments."
                )
            )


def _parse_document(xml_bytes):
    if isinstance(xml_bytes, str):
        xml_bytes = xml_bytes.encode("ISO-8859-1")
    if not isinstance(xml_bytes, bytes) or b"<!DOCTYPE" in xml_bytes.upper():
        raise ValidationError(_("DTE XML must be bytes without a DTD."))
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    try:
        return etree.fromstring(xml_bytes, parser=parser)
    except (etree.XMLSyntaxError, ValueError) as error:
        raise ValidationError(_("DTE XML is not well-formed: %s") % error) from error


@lru_cache(maxsize=1)
def _dte_schema():
    schema_path = Path(__file__).resolve().parent.parent / "static/xsd/DTE_v10.xsd"
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    try:
        return etree.XMLSchema(etree.parse(str(schema_path), parser))
    except (OSError, etree.XMLSchemaParseError, etree.XMLSyntaxError) as error:
        raise RuntimeError("The bundled SII DTE v10 XSD could not be loaded") from error


def _find_local_child(element, name):
    matches = [child for child in element if etree.QName(child).localname == name]
    return matches[0] if len(matches) == 1 else None


def _find_local_text(element, name):
    child = _find_local_child(element, name)
    return child.text if child is not None else None


def _require_supported_snapshot(snapshot):
    if not isinstance(snapshot, dict):
        raise ValidationError(_("The invoice snapshot must be a mapping."))
    if snapshot.get("document_code") not in {"33", "34"}:
        raise ValidationError(_("The selected DTE type is not supported."))
    if not snapshot.get("lines"):
        raise ValidationError(_("A DTE needs at least one detail line."))
    if len(snapshot["lines"]) > 60:
        raise ValidationError(_("A DTE supports at most 60 detail lines."))


def _add_text(parent, name, value):
    if value is None:
        raise ValidationError(_("The DTE field %s is required.") % name)
    element = etree.SubElement(parent, f"{{{SII_NAMESPACE}}}{name}")
    element.text = _format_value(value)
    return element


def _validate_reference_data(reference_data, index):
    required_fields = {"line_number", "document_type", "folio", "date", "reason"}
    if (
        not isinstance(reference_data, dict)
        or not required_fields.issubset(reference_data)
        or set(reference_data) - required_fields - {"reference_code"}
    ):
        raise ValidationError(_("The DTE reference is invalid."))
    if reference_data["line_number"] != index:
        raise ValidationError(_("DTE reference numbers must be sequential."))
    reference_code = reference_data.get("reference_code")
    if reference_code not in {None, "1", "2", "3"}:
        raise ValidationError(_("The DTE reference code is invalid."))
    return reference_code


def _add_optional_text(parent, name, value):
    if value not in (None, False, ""):
        return _add_text(parent, name, value)
    return None


def _format_value(value):
    if isinstance(value, float):
        value = Decimal(str(value))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValidationError(_("DTE numbers must be finite."))
        return format(value.normalize(), "f")
    value = str(value).strip()
    if not value:
        raise ValidationError(_("Required DTE values cannot be blank."))
    return value


def _tag(name, value):
    text = html.escape(_format_value(value), quote=False)
    try:
        text_bytes = text.encode("ISO-8859-1")
    except UnicodeEncodeError as error:
        raise ValidationError(
            _("TED values must be representable in ISO-8859-1.")
        ) from error
    return (
        b"<"
        + name.encode("ascii")
        + b">"
        + text_bytes
        + b"</"
        + name.encode("ascii")
        + b">"
    )


def _extract_caf_bytes(caf_file):
    matches = list(
        re.finditer(
            rb"<(?P<tag>(?:[A-Za-z_][\w.-]*:)?CAF)(?:\s[^>]*)?>.*?</(?P=tag)\s*>",
            caf_file,
            re.DOTALL,
        )
    )
    if len(matches) != 1:
        raise ValidationError(_("The CAF file must contain exactly one CAF section."))
    return matches[0].group(0)


def _extract_dd_bytes(xml_bytes):
    matches = list(
        re.finditer(
            rb"<(?P<tag>(?:[A-Za-z_][\w.-]*:)?DD)(?:\s[^>]*)?>.*?</(?P=tag)\s*>",
            xml_bytes,
            re.DOTALL,
        )
    )
    if len(matches) != 1:
        raise ValidationError(_("The DTE must contain exactly one serialized TED/DD."))
    return matches[0].group(0)
