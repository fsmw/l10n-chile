# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from decimal import Decimal

from lxml import etree

from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.services.ted_composer import DteTedComposer
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import (
    SII_NAMESPACE,
    _add_optional_text,
    _add_text,
    _dte_schema,
    _extract_dd_bytes,
    _find_local_child,
    _find_local_text,
    _parse_document,
)

EXPORT_CODES = frozenset({"110", "111", "112"})
PREFIX = f"{{{SII_NAMESPACE}}}"
MAX_REFERENCES = 40
# Aduana children in XSD order, from the snapshot customs keys.
CUSTOMS_HEAD = (
    ("sale_mode", "CodModVenta"),
    ("sale_clause", "CodClauVenta"),
    ("clause_total", "TotClauVenta"),
    ("transport_mode", "CodViaTransp"),
    ("transport_name", "NombreTransp"),
    ("carrier_vat", "RUTCiaTransp"),
    ("carrier_name", "NomCiaTransp"),
    ("transport_extra", "IdAdicTransp"),
    ("booking", "Booking"),
    ("operator", "Operador"),
    ("loading_port", "CodPtoEmbarque"),
    ("loading_extra", "IdAdicPtoEmb"),
    ("unloading_port", "CodPtoDesemb"),
    ("unloading_extra", "IdAdicPtoDesemb"),
    ("tare", "Tara"),
    ("tare_unit", "CodUnidMedTara"),
    ("gross_weight", "PesoBruto"),
    ("gross_unit", "CodUnidPesoBruto"),
    ("net_weight", "PesoNeto"),
    ("net_unit", "CodUnidPesoNeto"),
    ("total_items", "TotItems"),
    ("total_packages", "TotBultos"),
)
CUSTOMS_TAIL = (
    ("freight", "MntFlete"),
    ("insurance", "MntSeguro"),
    ("recipient_country", "CodPaisRecep"),
    ("destination_country", "CodPaisDestin"),
)
PACKAGE_FIELDS = (
    ("type", "CodTpoBultos"),
    ("quantity", "CantBultos"),
    ("marks", "Marcas"),
    ("container", "IdContainer"),
    ("seal", "Sello"),
    ("seal_issuer", "EmisorSello"),
)


class ExportXmlComposer:
    """Serialize and validate the SII Exportaciones payload of a 110."""

    def compose_ted(self, snapshot, caf_authorization, signature_service=None):
        """CAF-sign the TED with the export total in its transaction currency."""
        _require_export_snapshot(snapshot)
        return DteTedComposer().compose_ted(
            snapshot, caf_authorization, signature_service
        )

    def compose_unsigned_document(self, snapshot):
        """Serialize a captured export snapshot without doing arithmetic."""
        _require_export_snapshot(snapshot)
        root = etree.Element(PREFIX + "DTE", nsmap={None: SII_NAMESPACE}, version="1.0")
        code = snapshot["document_code"]
        document = etree.SubElement(
            root, PREFIX + "Exportaciones", ID=f"F{int(snapshot['folio'])}T{code}"
        )
        header = etree.SubElement(document, PREFIX + "Encabezado")
        self._append_identification(header, snapshot)
        self._append_issuer(header, snapshot["issuer"])
        self._append_receiver(header, snapshot["receiver"])
        self._append_customs(header, snapshot["customs"])
        self._append_totals(header, snapshot)
        for line in snapshot["lines"]:
            self._append_line(document, line)
        self._append_references(document, snapshot.get("references", []))
        ted_xml = snapshot.get("ted_xml")
        if ted_xml:
            etree.SubElement(document, PREFIX + "TED_PLACEHOLDER")
        _add_text(document, "TmstFirma", snapshot["document_timestamp"])
        try:
            xml_bytes = etree.tostring(
                root, encoding="ISO-8859-1", xml_declaration=True, pretty_print=True
            )
        except (LookupError, UnicodeEncodeError) as error:
            raise ValidationError(
                _("The export contains characters outside ISO-8859-1.")
            ) from error
        if ted_xml:
            placeholder = b"<TED_PLACEHOLDER/>"
            if xml_bytes.count(placeholder) != 1:
                raise ValidationError(_("The export TED placeholder is invalid."))
            xml_bytes = xml_bytes.replace(placeholder, ted_xml)
        return xml_bytes

    def validate_document(self, xml_bytes, signature_service=None, *, strict_sii=False):
        """Check the SII XSD, export totals, the TED and the XML signature."""
        root = _parse_document(xml_bytes)
        document = root.find(PREFIX + "Exportaciones")
        if root.tag != PREFIX + "DTE" or document is None:
            raise ValidationError(_("The XML must be a SII export DTE."))
        try:
            _dte_schema().assertValid(root)
        except etree.DocumentInvalid as error:
            raise ValidationError(
                _("The export does not comply with the SII DTE v10 schema: %s")
                % error.error_log.last_error
            ) from error
        self._validate_totals(document)
        signature_service = signature_service or SignatureService()
        ted = document.find(PREFIX + "TED")
        dd = _find_local_child(ted, "DD") if ted is not None else None
        caf = _find_local_child(dd, "CAF") if dd is not None else None
        frmt = _find_local_text(ted, "FRMT") if ted is not None else None
        if dd is None or caf is None or not frmt:
            raise ValidationError(_("The export TED is missing DD, CAF, or FRMT."))
        signature_service.verify_ted(_extract_dd_bytes(xml_bytes), frmt, caf)
        if strict_sii:
            signature_service.verify_signature(xml_bytes, sii_standalone=True)
        else:
            signature_service.verify_signature(xml_bytes)
        return True

    # Header -----------------------------------------------------------------

    def _append_identification(self, header, snapshot):
        identification = snapshot["identification"]
        node = etree.SubElement(header, PREFIX + "IdDoc")
        _add_text(node, "TipoDTE", snapshot["document_code"])
        _add_text(node, "Folio", snapshot["folio"])
        _add_text(node, "FchEmis", snapshot["issue_date"])
        _add_optional_text(node, "IndServicio", identification.get("service_indicator"))
        _add_optional_text(node, "FmaPagExp", identification.get("export_payment_code"))
        _add_optional_text(node, "FchVenc", identification.get("due_date"))

    def _append_issuer(self, header, issuer):
        node = etree.SubElement(header, PREFIX + "Emisor")
        _add_text(node, "RUTEmisor", issuer["vat"])
        _add_text(node, "RznSoc", issuer["name"][:100])
        _add_text(node, "GiroEmis", issuer["activity"][:80])
        _add_optional_text(node, "CorreoEmisor", issuer.get("email"))
        _add_text(node, "Acteco", issuer["activity_code"])
        _add_optional_text(node, "DirOrigen", (issuer.get("address") or "")[:70])
        _add_optional_text(node, "CmnaOrigen", (issuer.get("commune") or "")[:20])
        _add_optional_text(node, "CiudadOrigen", (issuer.get("city") or "")[:20])

    def _append_receiver(self, header, receiver):
        node = etree.SubElement(header, PREFIX + "Receptor")
        _add_text(node, "RUTRecep", receiver["vat"])
        _add_text(node, "RznSocRecep", receiver["name"][:100])
        # The 2026 SII matrix marks NumId as not applicable to exports.
        foreign = etree.SubElement(node, PREFIX + "Extranjero")
        _add_text(foreign, "Nacionalidad", receiver["nationality_code"])
        _add_optional_text(foreign, "IdAdicRecep", receiver.get("extra_id"))
        _add_optional_text(node, "DirRecep", (receiver.get("address") or "")[:70])
        _add_optional_text(node, "CiudadRecep", (receiver.get("city") or "")[:20])

    def _append_customs(self, header, customs):
        transport = etree.SubElement(header, PREFIX + "Transporte")
        node = etree.SubElement(transport, PREFIX + "Aduana")
        for key, name in CUSTOMS_HEAD:
            _add_optional_text(node, name, customs.get(key))
        for package in customs.get("packages", []):
            row = etree.SubElement(node, PREFIX + "TipoBultos")
            for key, name in PACKAGE_FIELDS:
                _add_optional_text(row, name, package.get(key))
        for key, name in CUSTOMS_TAIL:
            _add_optional_text(node, name, customs.get(key))

    def _append_totals(self, header, snapshot):
        totals = etree.SubElement(header, PREFIX + "Totales")
        _add_text(totals, "TpoMoneda", snapshot["currency"]["sii_name"])
        _add_text(totals, "MntExe", snapshot["totals"]["exempt"])
        _add_text(totals, "MntTotal", snapshot["totals"]["total"])
        other = snapshot["other_currency"]
        # A zero text correction has nothing to convert; the block is optional.
        if not Decimal(other["total"]):
            return
        node = etree.SubElement(header, PREFIX + "OtraMoneda")
        _add_text(node, "TpoMoneda", other["sii_name"])
        _add_text(node, "TpoCambio", other["rate"])
        _add_text(node, "MntExeOtrMnda", other["exempt"])
        _add_text(node, "MntTotOtrMnda", other["total"])

    # Body -------------------------------------------------------------------

    def _append_line(self, document, line):
        detail = etree.SubElement(document, PREFIX + "Detalle")
        _add_text(detail, "NroLinDet", line["sequence"])
        _add_text(detail, "IndExe", "1")
        _add_text(detail, "NmbItem", line["name"])
        _add_optional_text(detail, "DscItem", line.get("description"))
        _add_text(detail, "QtyItem", line["quantity"])
        _add_optional_text(detail, "UnmdItem", line.get("uom"))
        # The SII price type starts at 0.000001: a text correction omits it.
        if Decimal(line["unit_price"]) > 0:
            _add_text(detail, "PrcItem", line["unit_price"])
        _add_text(detail, "MontoItem", line["subtotal"])

    def _append_references(self, document, references):
        if len(references) > MAX_REFERENCES:
            raise ValidationError(_("An export supports at most 40 references."))
        for index, reference in enumerate(references, start=1):
            node = etree.SubElement(document, PREFIX + "Referencia")
            _add_text(node, "NroLinRef", index)
            _add_text(node, "TpoDocRef", reference["document_type"])
            _add_text(node, "FolioRef", reference["folio"])
            _add_text(node, "FchRef", reference["date"])
            _add_optional_text(node, "CodRef", reference.get("code"))
            _add_optional_text(node, "RazonRef", reference.get("reason"))

    def _validate_totals(self, document):
        def amount(path):
            return Decimal(document.findtext(path) or "0")

        lines = sum(
            (
                Decimal(detail.findtext(PREFIX + "MontoItem"))
                for detail in document.findall(PREFIX + "Detalle")
            ),
            Decimal(0),
        )
        totals = PREFIX + "Encabezado/" + PREFIX + "Totales/" + PREFIX
        if not lines == amount(totals + "MntExe") == amount(totals + "MntTotal"):
            raise ValidationError(
                _("The export totals differ from the sum of its lines.")
            )


def _require_export_snapshot(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        raise ValidationError(_("The export snapshot is not supported."))
    if snapshot.get("document_code") not in EXPORT_CODES:
        raise ValidationError(_("The export document type is not supported."))
    if not 1 <= len(snapshot.get("lines") or []) <= 60:
        raise ValidationError(_("An export needs between one and 60 detail lines."))
