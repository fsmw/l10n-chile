# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from decimal import Decimal

from lxml import etree

from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.services.dte_wire_format import (
    fixed_decimal,
    serialize_unsigned_dte,
)
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
    _validate_reference_data,
)

LIQUIDATION_CODES = frozenset({"43"})
PREFIX = f"{{{SII_NAMESPACE}}}"
MAX_LINES = 60
MAX_CHARGES = 20
MAX_REFERENCES = 40


class LiquidationXmlComposer:
    """Serialize and validate the SII Liquidacion payload of a DTE 43.

    DTE_v10.xsd defines the settlement invoice as its own ``Liquidacion``
    element: TpoDocLiq on each Detalle, a Comisiones block after Referencia,
    and the Comisiones totals inside Totales. The wire layout, TED and
    signature are those of invoices 33 and 34.
    """

    def compose_ted(
        self,
        snapshot,
        caf_authorization,
        signature_service=None,
        *,
        invoice_layout=True,
    ):
        """CAF-sign the TED with the settlement total (MntTotal)."""
        _require_liquidation_snapshot(snapshot)
        return DteTedComposer().compose_ted(
            snapshot,
            caf_authorization,
            signature_service,
            indented_layout=invoice_layout,
        )

    def compose_unsigned_document(self, snapshot):
        """Serialize a captured snapshot in the invoice wire layout."""
        _require_liquidation_snapshot(snapshot)
        root = etree.Element(PREFIX + "DTE", nsmap={None: SII_NAMESPACE}, version="1.0")
        code = snapshot["document_code"]
        document = etree.SubElement(
            root, PREFIX + "Liquidacion", ID=f"F{int(snapshot['folio'])}T{code}"
        )
        header = etree.SubElement(document, PREFIX + "Encabezado")
        self._append_identification(header, snapshot)
        self._append_issuer(header, snapshot["issuer"])
        self._append_receiver(header, snapshot["receiver"])
        self._append_totals(header, snapshot["totals"])
        for index, line in enumerate(snapshot["lines"], start=1):
            self._append_line(document, index, line)
        self._append_references(document, snapshot.get("references") or [])
        for index, charge in enumerate(snapshot["charges"], start=1):
            self._append_charge(document, index, charge)
        ted_xml = snapshot.get("ted_xml")
        if ted_xml:
            etree.SubElement(document, PREFIX + "TED_PLACEHOLDER")
        _add_text(document, "TmstFirma", snapshot["document_timestamp"])
        try:
            return serialize_unsigned_dte(root, document, ted_xml, root_indent=2)
        except ValueError as error:
            raise ValidationError(
                _("The settlement invoice TED placeholder is invalid.")
            ) from error

    def validate_document(self, xml_bytes, signature_service=None, *, strict_sii=False):
        """Check the SII XSD, the settlement totals, the TED and the signature."""
        root = _parse_document(xml_bytes)
        document = root.find(PREFIX + "Liquidacion")
        if root.tag != PREFIX + "DTE" or document is None:
            raise ValidationError(_("The XML must be a SII settlement invoice DTE."))
        try:
            _dte_schema().assertValid(root)
        except etree.DocumentInvalid as error:
            raise ValidationError(
                _(
                    "The settlement invoice does not comply with the SII DTE v10 "
                    "schema: %s"
                )
                % error.error_log.last_error
            ) from error
        self._validate_totals(document)
        signature_service = signature_service or SignatureService()
        ted = document.find(PREFIX + "TED")
        dd = _find_local_child(ted, "DD") if ted is not None else None
        caf = _find_local_child(dd, "CAF") if dd is not None else None
        frmt = _find_local_text(ted, "FRMT") if ted is not None else None
        if dd is None or caf is None or not frmt:
            raise ValidationError(_("The TED is missing DD, CAF, or FRMT."))
        signature_service.verify_ted(_extract_dd_bytes(xml_bytes), frmt, caf)
        signature_service.verify_signature(xml_bytes, sii_standalone=strict_sii)
        return True

    # Header ------------------------------------------------------------------

    @staticmethod
    def _append_identification(header, snapshot):
        node = etree.SubElement(header, PREFIX + "IdDoc")
        _add_text(node, "TipoDTE", snapshot["document_code"])
        _add_text(node, "Folio", snapshot["folio"])
        _add_text(node, "FchEmis", snapshot["issue_date"])
        _add_optional_text(node, "FmaPago", snapshot.get("payment_form"))
        _add_optional_text(node, "FchVenc", snapshot.get("due_date"))

    @staticmethod
    def _append_issuer(header, issuer):
        node = etree.SubElement(header, PREFIX + "Emisor")
        _add_text(node, "RUTEmisor", issuer["vat"])
        _add_text(node, "RznSoc", issuer["name"][:100])
        _add_text(node, "GiroEmis", issuer["activity"][:80])
        _add_optional_text(node, "CorreoEmisor", issuer.get("email"))
        _add_text(node, "Acteco", issuer["activity_code"])
        _add_text(node, "DirOrigen", issuer["address"][:70])
        _add_optional_text(node, "CmnaOrigen", (issuer.get("commune") or "")[:20])
        _add_optional_text(node, "CiudadOrigen", (issuer.get("city") or "")[:20])

    @staticmethod
    def _append_receiver(header, receiver):
        node = etree.SubElement(header, PREFIX + "Receptor")
        _add_text(node, "RUTRecep", receiver["vat"])
        _add_text(node, "RznSocRecep", receiver["name"][:100])
        _add_text(node, "GiroRecep", receiver["activity"][:40])
        _add_optional_text(node, "Contacto", (receiver.get("contact") or "")[:80])
        _add_optional_text(node, "CorreoRecep", receiver.get("email"))
        _add_text(node, "DirRecep", receiver["address"][:70])
        _add_text(node, "CmnaRecep", receiver["commune"][:20])
        _add_optional_text(node, "CiudadRecep", (receiver.get("city") or "")[:20])

    @staticmethod
    def _append_totals(header, totals_data):
        """Serialize Totales in XSD order, with the Comisiones totals."""
        totals = etree.SubElement(header, PREFIX + "Totales")
        _add_optional_text(totals, "MntNeto", totals_data.get("net"))
        _add_optional_text(totals, "MntExe", totals_data.get("exempt"))
        if totals_data.get("vat_rate"):
            _add_text(totals, "TasaIVA", fixed_decimal(totals_data["vat_rate"], 2))
        _add_optional_text(totals, "IVA", totals_data.get("vat"))
        commissions = etree.SubElement(totals, PREFIX + "Comisiones")
        _add_optional_text(commissions, "ValComNeto", totals_data.get("commission_net"))
        _add_optional_text(
            commissions, "ValComExe", totals_data.get("commission_exempt")
        )
        _add_optional_text(commissions, "ValComIVA", totals_data.get("commission_vat"))
        _add_text(totals, "MntTotal", totals_data["total"])

    # Body --------------------------------------------------------------------

    @staticmethod
    def _append_line(document, index, line):
        detail = etree.SubElement(document, PREFIX + "Detalle")
        _add_text(detail, "NroLinDet", index)
        _add_text(detail, "TpoDocLiq", line["settled_document_code"])
        if line.get("exempt"):
            _add_text(detail, "IndExe", "1")
        _add_text(detail, "NmbItem", line["name"])
        _add_optional_text(detail, "DscItem", line.get("description"))
        _add_text(detail, "QtyItem", fixed_decimal(line["quantity"], 6))
        _add_optional_text(detail, "UnmdItem", line.get("uom"))
        _add_text(detail, "PrcItem", fixed_decimal(line["unit_price"], 6))
        _add_text(detail, "MontoItem", line["subtotal"])

    @staticmethod
    def _append_references(document, references):
        if len(references) > MAX_REFERENCES:
            raise ValidationError(_("A DTE supports at most 40 references."))
        for index, reference in enumerate(references, start=1):
            reference_code = _validate_reference_data(reference, index)
            node = etree.SubElement(document, PREFIX + "Referencia")
            _add_text(node, "NroLinRef", index)
            _add_text(node, "TpoDocRef", reference["document_type"])
            _add_text(node, "FolioRef", reference["folio"])
            _add_text(node, "FchRef", reference["date"])
            _add_optional_text(node, "CodRef", reference_code)
            _add_text(node, "RazonRef", reference["reason"])

    @staticmethod
    def _append_charge(document, index, charge):
        node = etree.SubElement(document, PREFIX + "Comisiones")
        _add_text(node, "NroLinCom", index)
        _add_text(node, "TipoMovim", charge["movement"])
        _add_text(node, "Glosa", charge["description"])
        _add_text(node, "ValComNeto", charge["net"])
        _add_text(node, "ValComExe", charge["exempt"])
        _add_optional_text(node, "ValComIVA", charge.get("vat"))

    # Validation --------------------------------------------------------------

    @staticmethod
    def _validate_totals(document):
        """Recompute Totales from Detalle and Comisiones (format fields 107-124)."""

        def amount(node, name):
            return Decimal(node.findtext(PREFIX + name) or "0")

        totals = document.find(f"{PREFIX}Encabezado/{PREFIX}Totales")
        header_charges = totals.find(PREFIX + "Comisiones")
        details = document.findall(PREFIX + "Detalle")
        charges = document.findall(PREFIX + "Comisiones")
        exempt = sum(
            (amount(d, "MontoItem") for d in details if amount(d, "IndExe") == 1),
            Decimal(0),
        )
        net = sum((amount(d, "MontoItem") for d in details), Decimal(0)) - exempt
        expected = {
            name: sum((amount(c, name) for c in charges), Decimal(0))
            for name in ("ValComNeto", "ValComExe", "ValComIVA")
        }
        mismatch = (
            header_charges is None
            or not charges
            or net != amount(totals, "MntNeto")
            or exempt != amount(totals, "MntExe")
            or any(amount(header_charges, k) != v for k, v in expected.items())
            or amount(totals, "MntTotal")
            != net + exempt + amount(totals, "IVA") - sum(expected.values())
        )
        if mismatch:
            raise ValidationError(
                _(
                    "The settlement invoice totals differ from its detail and "
                    "commission lines."
                )
            )


def _require_liquidation_snapshot(snapshot):
    if not isinstance(snapshot, dict):
        raise ValidationError(_("The settlement invoice snapshot must be a mapping."))
    if snapshot.get("document_code") not in LIQUIDATION_CODES:
        raise ValidationError(_("The settlement invoice document type is not 43."))
    if not 1 <= len(snapshot.get("lines") or []) <= MAX_LINES:
        raise ValidationError(_("A DTE 43 needs between one and 60 detail lines."))
    if not 1 <= len(snapshot.get("charges") or []) <= MAX_CHARGES:
        raise ValidationError(
            _("A DTE 43 needs between one and 20 commission or other charge lines.")
        )
