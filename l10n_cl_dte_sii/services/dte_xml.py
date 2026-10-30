# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from lxml import etree

from odoo import _, fields
from odoo.exceptions import ValidationError
from odoo.tools.float_utils import float_repr, float_round

from .xml_signature import DSIG_NS, sign_with_certificate

DTE_NS = "http://www.sii.cl/SiiDte"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
XSD_DIRECTORY = Path(__file__).resolve().parents[1] / "data/xsd"
SII_RECEIVER_RUT = "60803000-K"
SII_UOM_CODES = {
    "uom.product_uom_unit": "U",
    "uom.product_uom_dozen": "DOC",
    "uom.product_uom_meter": "MT",
    "uom.product_uom_foot": "P2",
    "uom.product_uom_kgm": "KN",
    "uom.product_uom_litre": "LT",
    "uom.product_uom_gram": "GN",
}


def _tag(name):
    return f"{{{DTE_NS}}}{name}"


def _add(parent, name, value=None, **attributes):
    node = etree.SubElement(parent, _tag(name), **attributes)
    if value is not None and value is not False:
        node.text = str(value)
    return node


def _plain(parent, name, value=None, **attributes):
    node = etree.SubElement(parent, name, **attributes)
    if value is not None and value is not False:
        node.text = str(value)
    return node


def _put(parent, name, value, length=None):
    if value is None or value is False or value == "":
        return None
    value = str(value).strip()
    if length:
        value = value[:length]
    return _add(parent, name, value)


def normalize_rut(value):
    value = (value or "").replace(".", "").replace(" ", "").upper()
    if value.startswith("CL"):
        value = value[2:].lstrip("-")
    return value if "-" in value else f"{value[:-1]}-{value[-1:]}"


@lru_cache(maxsize=3)
def _sii_schema(schema_name):
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    schema_path = XSD_DIRECTORY / schema_name
    return etree.XMLSchema(etree.parse(str(schema_path), parser))


def validate_dte_xml(dte, document_code, context=None):
    """Validate an individual signed DTE against its supported local SII XSD."""
    context = context or {}
    # The official v10 DTE schema intentionally excludes 39/41. Their inline
    # schema lives in EnvioBOLETA_v11 and validates the nested DTE with its
    # envelope in validate_dte_envelope().
    if str(document_code) in {"39", "41"}:
        return
    try:
        _sii_schema("DTE_v10.xsd").assertValid(dte)
    except etree.DocumentInvalid as error:
        raise ValidationError(
            _("The DTE XML does not conform to the SII schema: %s", error)
        ) from error


def validate_dte_envelope(envelope, document_code, context=None):
    """Validate a signed DTE submission envelope using its document family XSD."""
    context = context or {}
    schema_name = (
        "EnvioBOLETA_v11.xsd"
        if str(document_code) in {"39", "41"}
        else "EnvioDTE_v10.xsd"
    )
    try:
        _sii_schema(schema_name).assertValid(envelope)
    except etree.DocumentInvalid as error:
        raise ValidationError(
            _("The DTE envelope does not conform to the SII schema: %s", error)
        ) from error


def _caf_nodes(caf_root):
    caf = caf_root.xpath("//*[local-name()='AUTORIZACION']/*[local-name()='CAF']")
    if not caf:
        caf = caf_root.xpath("//*[local-name()='CAF']")
    if not caf:
        raise ValidationError(_("The CAF XML has no CAF element."))
    caf = caf[0]
    da = caf.find("DA")
    frma = caf.find("FRMA")
    rsask = caf.findtext("RSASK")
    if not rsask:
        # El CAF emitido por el SII trae RSASK como hermano de CAF, dentro de
        # AUTORIZACION; el formato aceptado también admite RSASK anidado.
        siblings = caf.getparent().xpath("./*[local-name()='RSASK']")
        rsask = siblings[0].text if siblings else None
    if da is None or frma is None or not frma.text or not rsask:
        raise ValidationError(_("The CAF must contain DA, FRMA and its private key."))
    return caf, da, frma, rsask


def _strip_layout_whitespace(node):
    """Elimina sangría y saltos de línea de un subárbol.

    El SII verifica el TED sobre el `DD` con `\n\s*` eliminado, así que el
    documento emitido no debe conservar la sangría del CAF ni firmar bytes que
    el receptor va a normalizar.
    """
    for element in node.iter():
        if element.text is not None and not element.text.strip():
            element.text = None
        if element.tail is not None and not element.tail.strip():
            element.tail = None


def _clone_in_dte_namespace(node):
    clone = etree.Element(_tag(etree.QName(node).localname))
    for key, value in node.attrib.items():
        clone.set(etree.QName(key).localname, value)
    clone.text = node.text
    clone.tail = node.tail
    for child in node:
        clone.append(_clone_in_dte_namespace(child))
    return clone


class DteXmlBuilder:
    """Build SII DTE, TED and signed envelopes for outgoing Chilean invoices."""

    document_tag = "Documento"

    def __init__(self, move, folio, caf_root, certificate):
        self.move = move
        self.company = move.company_id.sudo()
        self.folio = int(folio)
        self.caf_root = caf_root
        self.certificate = certificate
        self.code = str(move.l10n_latam_document_type_id.code)
        self.doc_id = f"F{self.folio}T{self.code}"
        self.stamp = self._timestamp()
        self.caf, self.caf_da, self.caf_frma, self.caf_private_key = _caf_nodes(
            caf_root
        )
        self.amounts = move._l10n_cl_get_amounts()

    def build(self):
        self._validate_caf_identity()
        dte = etree.Element(
            _tag("DTE"), nsmap={None: DTE_NS, "xsi": XSI_NS}, version="1.0"
        )
        document = _add(dte, self.document_tag, ID=self.doc_id)
        self._build_header(document)
        self._build_detail(document)
        self._build_references(document)
        self._build_ted(document)
        sign_with_certificate(
            dte,
            self.doc_id,
            self.certificate,
            namespace_map={None: DSIG_NS, "xsi": XSI_NS},
        )
        validate_dte_xml(dte, self.code, context=self.move.env.context)
        return dte

    def build_envelope(self, signed_dte, receiver_rut=SII_RECEIVER_RUT):
        is_ticket = self.code in {"39", "41"}
        root_name = "EnvioBOLETA" if is_ticket else "EnvioDTE"
        schema_name = "EnvioBOLETA_v11.xsd" if is_ticket else "EnvioDTE_v10.xsd"
        envelope = etree.Element(
            _tag(root_name),
            nsmap={None: DTE_NS, "xsi": XSI_NS},
            attrib={
                f"{{{XSI_NS}}}schemaLocation": f"{DTE_NS} {schema_name}",
                "version": "1.0",
            },
        )
        set_dte = _add(envelope, "SetDTE", ID="SetDoc")
        cover = _add(set_dte, "Caratula", version="1.0")
        company = self.company
        cert_rut = self.certificate.subject_serial_number
        if not cert_rut:
            raise ValidationError(_("The SII certificate must include the sender RUT."))
        _add(cover, "RutEmisor", normalize_rut(company.vat))
        _add(cover, "RutEnvia", normalize_rut(cert_rut))
        _add(cover, "RutReceptor", normalize_rut(receiver_rut))
        _add(
            cover,
            "FchResol",
            fields.Date.to_string(company.l10n_cl_dte_resolution_date),
        )
        _add(cover, "NroResol", company.l10n_cl_dte_resolution_number or "0")
        _add(cover, "TmstFirmaEnv", self.stamp)
        subtotal = _add(cover, "SubTotDTE")
        _add(subtotal, "TpoDTE", self.code)
        _add(subtotal, "NroDTE", "1")
        set_dte.append(deepcopy(signed_dte))
        sign_with_certificate(
            envelope,
            "SetDoc",
            self.certificate,
            namespace_map={None: DSIG_NS, "xsi": XSI_NS},
        )
        validate_dte_envelope(envelope, self.code, context=self.move.env.context)
        return envelope

    def _timestamp(self):
        now = fields.Datetime.context_timestamp(self.move, fields.Datetime.now())
        return now.strftime("%Y-%m-%dT%H:%M:%S")

    @staticmethod
    def _format_uom(uom):
        if not uom:
            return ""
        metadata = uom.get_metadata()
        xml_id = metadata[0].get("xmlid") if metadata else False
        return SII_UOM_CODES.get(xml_id, uom.name[:4])

    def _validate_caf_identity(self):
        da = self.caf_da
        try:
            issuer = da.findtext("RE")
            doc_code = da.findtext("TD")
            start = int(da.findtext("RNG/D"))
            end = int(da.findtext("RNG/H"))
        except (TypeError, ValueError) as error:
            raise ValidationError(_("The CAF range metadata is invalid.")) from error
        if normalize_rut(issuer) != normalize_rut(self.move.company_id.vat):
            raise ValidationError(_("The CAF belongs to another issuer RUT."))
        if str(doc_code) != self.code or not start <= self.folio <= end:
            raise ValidationError(_("The CAF does not authorize this type and folio."))

    def _receiver_rut(self):
        partner = self.move.commercial_partner_id
        if (
            partner.country_id.code != "CL"
            and getattr(partner, "l10n_cl_sii_taxpayer_type", False) == "4"
        ):
            return "55555555-5"
        return normalize_rut(partner.vat)

    def _activity_codes(self):
        """Códigos Acteco del emisor: catálogo primero, Char heredado de respaldo."""
        codes = self.company.l10n_cl_dte_activity_ids.sorted("code").mapped("code")
        if codes:
            return codes
        legacy = (self.company.l10n_cl_dte_activity_code or "").strip()
        return [legacy] if legacy else []

    def _build_header(self, document):
        move = self.move
        company = self.company
        partner = move.commercial_partner_id
        code = self.code
        voucher = code in {"39", "41"}
        header = _add(document, "Encabezado")
        id_doc = _add(header, "IdDoc")
        _add(id_doc, "TipoDTE", code)
        _add(id_doc, "Folio", self.folio)
        _add(id_doc, "FchEmis", fields.Date.to_string(move.invoice_date))
        if voucher:
            _add(id_doc, "IndServicio", "3")
            if move.invoice_line_ids:
                _add(id_doc, "IndMntNeto", "2")
        else:
            payment_term_code = getattr(
                move.invoice_payment_term_id, "l10n_cl_sii_code", False
            )
            _add(id_doc, "FmaPago", payment_term_code or "2")
            _add(
                id_doc,
                "FchVenc",
                fields.Date.to_string(move.invoice_date_due or move.invoice_date),
            )

        issuer = _add(header, "Emisor")
        _add(issuer, "RUTEmisor", normalize_rut(company.vat))
        _add(
            issuer,
            "RznSocEmisor" if voucher else "RznSoc",
            company.partner_id.name,
        )
        _put(
            issuer,
            "GiroEmisor" if voucher else "GiroEmis",
            company.l10n_cl_dte_activity,
            80,
        )
        if company.phone and not voucher:
            _put(issuer, "Telefono", company.phone, 20)
        if company.l10n_cl_dte_email and not voucher:
            _put(issuer, "CorreoEmisor", company.l10n_cl_dte_email, 80)
        if not voucher:
            for activity_code in self._activity_codes():
                _add(issuer, "Acteco", activity_code)
        journal = move.journal_id
        _put(
            issuer,
            "CdgSIISucur" if voucher else "Sucursal",
            journal.l10n_cl_dte_sucursal,
            9,
        )
        _put(
            issuer,
            "DirOrigen",
            " ".join(
                filter(None, [company.partner_id.street, company.partner_id.street2])
            ),
            70,
        )
        _put(issuer, "CmnaOrigen", company.partner_id.city, 20)
        if move.user_id and not voucher:
            _put(issuer, "CdgVendedor", move.user_id.name, 60)

        recipient = _add(header, "Receptor")
        _add(recipient, "RUTRecep", self._receiver_rut())
        _put(recipient, "RznSocRecep", partner.name, 100)
        if not voucher:
            _put(recipient, "GiroRecep", partner.l10n_cl_activity_description, 40)
            _put(recipient, "Contacto", partner.phone or partner.email, 80)
            _put(
                recipient, "CorreoRecep", partner.l10n_cl_dte_email or partner.email, 80
            )
            _put(
                recipient,
                "DirRecep",
                " ".join(filter(None, [partner.street, partner.street2])),
                70,
            )
        _put(recipient, "CmnaRecep", partner.city, 20)
        self._build_totals(header)

    def _build_totals(self, header):
        totals = _add(header, "Totales")
        amounts = self.amounts
        if amounts.get("main_currency_name"):
            _add(totals, "TpoMoneda", amounts["main_currency_name"])
        rounding = amounts["main_currency_round"]
        for key, tag in (
            ("subtotal_amount_taxable", "MntNeto"),
            ("subtotal_amount_exempt", "MntExe"),
        ):
            if amounts.get(key):
                _add(
                    totals,
                    tag,
                    float_repr(float_round(amounts[key], rounding), rounding),
                )
        if amounts.get("vat_percent") and self.code not in {"39", "41"}:
            _add(totals, "TasaIVA", amounts["vat_percent"])
        if amounts.get("vat_amount"):
            _add(
                totals,
                "IVA",
                float_repr(float_round(amounts["vat_amount"], rounding), rounding),
            )
        for tax in self.move._l10n_cl_get_withholdings():
            withholding = _add(totals, "ImptoReten")
            _add(withholding, "TipoImp", tax["tax_code"])
            _add(withholding, "TasaImp", tax["tax_percent"])
            _add(withholding, "MontoImp", float_repr(tax["tax_amount"], 0))
        _add(totals, "MntTotal", float_repr(amounts["total_amount"], 0))
        if amounts.get("second_currency"):
            secondary = amounts["second_currency"]
            other = _add(header, "OtraMoneda")
            _add(other, "TpoMoneda", secondary["name"])
            _add(other, "TpoCambio", float_repr(secondary["rate"], 2))
            for key, tag in (
                ("subtotal_amount_taxable", "MntNetoOtrMnda"),
                ("subtotal_amount_exempt", "MntExeOtrMnda"),
                ("vat_amount", "IVAOtrMnda"),
            ):
                if secondary.get(key):
                    _add(
                        other,
                        tag,
                        float_repr(secondary[key], secondary["round_currency"]),
                    )
            _add(
                other,
                "MntTotOtrMnda",
                float_repr(secondary["total_amount"], secondary["round_currency"]),
            )

    def _build_detail(self, document):
        move = self.move
        lines = move.invoice_line_ids.filtered(
            lambda line: line.display_type == "product" and line.price_subtotal >= 0
        )
        for number, line in enumerate(lines, start=1):
            amounts = line._l10n_cl_get_line_amounts()
            detail = _add(document, "Detalle")
            _add(detail, "NroLinDet", number)
            if line.product_id.default_code:
                code = _add(detail, "CdgItem")
                _add(code, "TpoCodigo", "INT1")
                _add(code, "VlrCodigo", line.product_id.default_code[:35])
            if amounts.get("exempt"):
                _add(detail, "IndExe", "1")
            _put(detail, "NmbItem", line.product_id.name or line.name, 80)
            _put(detail, "DscItem", line.name, 1000)
            if line.price_unit or line.price_total:
                _add(detail, "QtyItem", float_repr(line.quantity, 6))
                _put(detail, "UnmdItem", self._format_uom(line.product_uom_id), 4)
                _add(detail, "PrcItem", float_repr(amounts["price_item"], 6))
            if amounts.get("second_currency"):
                second = amounts["second_currency"]
                other_currency = _add(detail, "OtrMnda")
                _add(
                    other_currency,
                    "PrcOtrMon",
                    float_repr(second["price"], second["round_currency"]),
                )
                _add(other_currency, "Moneda", line.move_id.currency_id.name)
                _add(other_currency, "FctConv", second["conversion_rate"])
                if second.get("amount_discount"):
                    _add(
                        other_currency,
                        "DctoOtrMnda",
                        float_repr(second["amount_discount"], second["round_currency"]),
                    )
                _add(
                    other_currency,
                    "MontoItemOtrMnda",
                    float_repr(second["total_amount"], second["round_currency"]),
                )
            if line.discount:
                _add(detail, "DescuentoPct", float_repr(line.discount, 2))
                _add(detail, "DescuentoMonto", float_repr(amounts["total_discount"], 0))
            for tax in line.tax_ids.filtered(
                lambda record: record.l10n_cl_sii_code not in (False, 14)
            ):
                _add(detail, "CodImpAdic", tax.l10n_cl_sii_code)
            _add(
                detail,
                "MontoItem",
                float_repr(amounts["price_subtotal"], amounts["decimal_places"]),
            )
        discounts = self.amounts.get("global_discounts", [])
        for number, discount in enumerate(discounts, start=1):
            node = _add(document, "DscRcgGlobal")
            _add(node, "NroLinDR", number)
            _add(node, "TpoMov", "D")
            _put(node, "GlosaDR", discount["name"], 40)
            _add(node, "TpoValor", "$")
            _add(
                node,
                "ValorDR",
                float_repr(
                    discount["global_discount_main_value"],
                    self.amounts["main_currency_round"],
                ),
            )
            if discount.get("global_discount_second_value"):
                _add(
                    node,
                    "ValorDROtrMnda",
                    float_repr(
                        discount["global_discount_second_value"],
                        self.amounts["main_currency_round"],
                    ),
                )
            if not discount.get("tax_ids"):
                _add(node, "IndExeDR", "1")

    def _build_references(self, document):
        for number, reference in enumerate(self.move.l10n_cl_reference_ids, start=1):
            node = _add(document, "Referencia")
            _add(node, "NroLinRef", number)
            _add(node, "TpoDocRef", reference.l10n_cl_reference_doc_type_id.code)
            _add(node, "FolioRef", (reference.origin_doc_number or "")[:18])
            if reference.date:
                _add(node, "FchRef", fields.Date.to_string(reference.date))
            _put(node, "CodRef", reference.reference_doc_code)
            _put(node, "RazonRef", reference.reason, 90)

    def _build_ted(self, document):
        move = self.move
        dd = _add(document, "TED", version="1.0")
        unsigned_dd = etree.Element("DD")
        _plain(unsigned_dd, "RE", normalize_rut(move.company_id.vat))
        _plain(unsigned_dd, "TD", self.code)
        _plain(unsigned_dd, "F", self.folio)
        _plain(unsigned_dd, "FE", fields.Date.to_string(move.invoice_date))
        _plain(unsigned_dd, "RR", self._receiver_rut())
        _plain(unsigned_dd, "RSR", (move.commercial_partner_id.name or "")[:40])
        _plain(unsigned_dd, "MNT", self._ted_amount())
        line = move.invoice_line_ids.filtered(
            lambda record: record.display_type == "product"
        )[:1]
        _plain(
            unsigned_dd,
            "IT1",
            ((line.product_id.name or line.name)[:40]) if line else "",
        )
        caf_copy_plain = _plain(
            unsigned_dd, "CAF", version=self.caf.get("version", "1.0")
        )
        caf_copy_plain.append(deepcopy(self.caf_da))
        caf_copy_plain.append(deepcopy(self.caf_frma))
        _plain(unsigned_dd, "TSTED", self.stamp)
        _strip_layout_whitespace(unsigned_dd)
        data = _add(dd, "DD")
        for child in unsigned_dd:
            data.append(_clone_in_dte_namespace(child))
        _strip_layout_whitespace(data)
        signed_bytes = etree.tostring(
            unsigned_dd, encoding="ISO-8859-1", xml_declaration=False
        )
        signed = self.move.env["certificate.key"]._sign_with_key(
            signed_bytes,
            base64.b64encode(self.caf_private_key.encode("utf-8")),
            hashing_algorithm="sha1",
            formatting="base64",
        )
        etree.SubElement(
            dd, _tag("FRMT"), algoritmo="SHA1withRSA"
        ).text = signed.decode("ascii")
        _add(document, "TmstFirma", self.stamp)

    def _ted_amount(self):
        return float_repr(self.amounts["total_amount"], 0)


def serialize_xml(element):
    return etree.tostring(
        element,
        xml_declaration=True,
        encoding="ISO-8859-1",
        pretty_print=False,
    )
