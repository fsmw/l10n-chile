# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from lxml import etree

from odoo import api, models

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.services.ted_composer import DteTedComposer

SII_NAMESPACE = "http://www.sii.cl/SiiDte"
DOCUMENT_CODE = "52"
VAT_RATE_PERCENT = 19
# IndExe values from DTE_v10.xsd: 1 = not subject to VAT, 2 = not billable.
EXEMPT_INDICATOR = "1"
UNVALUED_INDICATOR = "2"
TAXABLE = "taxable"
EXEMPT = "exempt"
UNVALUED = "unvalued"
TED_PLACEHOLDER = "TED_PLACEHOLDER"


def _text(parent, tag, value):
    element = etree.SubElement(parent, f"{{{SII_NAMESPACE}}}{tag}")
    element.text = str(value)
    return element


def _optional_text(parent, tag, value):
    if value:
        return _text(parent, tag, value)
    return None


class DteDispatchXmlComposer(models.AbstractModel):
    _name = "l10n_cl.dte.dispatch.xml.composer"
    _description = "DTE 52 XML composer"

    @api.model
    def compose_signed_guide(
        self,
        dispatch,
        folio,
        issuer,
        receiver,
        timestamp,
        caf_authorization,
        signing_environment,
        transport=None,
    ):
        """Return the signed DTE 52 XML bytes for a dispatch.

        The TED is built and CAF-signed first, because DTE_v10 requires it
        before the signature timestamp. ``signing_environment`` is the
        signature service environment name, chosen by the caller.
        """
        lines = self._sorted_lines(dispatch)
        ted_xml = DteTedComposer().compose_ted(
            self._ted_snapshot(dispatch, folio, issuer, receiver, timestamp, lines),
            caf_authorization,
        )
        unsigned_xml = self.compose_unsigned_guide(
            dispatch,
            folio,
            issuer,
            receiver,
            timestamp,
            transport=transport,
            ted_xml=ted_xml,
        )
        return SignatureService().sign_document(
            dispatch.company_id, signing_environment, unsigned_xml
        )

    @api.model
    def compose_unsigned_guide(
        self, dispatch, folio, issuer, receiver, timestamp, transport=None, ted_xml=None
    ):
        """Return the unsigned DTE 52 XML bytes (ISO-8859-1) for a dispatch.

        ``issuer``, ``receiver`` and ``transport`` are plain mappings, so the
        caller decides where the values come from. ``transport`` adds the
        Transporte block only when it is not empty. ``ted_xml`` is the CAF-signed
        TED; without it the document is not schema-valid.
        """
        root = etree.Element(
            f"{{{SII_NAMESPACE}}}DTE", nsmap={None: SII_NAMESPACE}, version="1.0"
        )
        document = etree.SubElement(
            root,
            f"{{{SII_NAMESPACE}}}Documento",
            ID=f"F{int(folio):010d}T{DOCUMENT_CODE}",
        )
        header = etree.SubElement(document, f"{{{SII_NAMESPACE}}}Encabezado")
        self._append_id_doc(header, dispatch, folio)
        self._append_issuer(header, issuer)
        self._append_receiver(header, receiver, transport)
        self._append_totals(header, dispatch.line_ids)
        for index, line in enumerate(self._sorted_lines(dispatch), start=1):
            self._append_line(document, index, line)
        if ted_xml:
            etree.SubElement(document, f"{{{SII_NAMESPACE}}}{TED_PLACEHOLDER}")
        _text(document, "TmstFirma", timestamp)
        xml_bytes = etree.tostring(
            root, encoding="ISO-8859-1", xml_declaration=True, pretty_print=True
        )
        if ted_xml:
            xml_bytes = xml_bytes.replace(f"<{TED_PLACEHOLDER}/>".encode(), ted_xml)
        return xml_bytes

    @api.model
    def _sorted_lines(self, dispatch):
        return dispatch.line_ids.sorted(lambda line: (line.sequence, line.id))

    @api.model
    def _ted_snapshot(self, dispatch, folio, issuer, receiver, timestamp, lines):
        totals = self._compute_totals(lines)
        return {
            "document_code": DOCUMENT_CODE,
            "folio": int(folio),
            "company_id": dispatch.company_id.id,
            "issuer": {"vat": issuer["vat"]},
            "receiver": {"vat": receiver["vat"], "name": receiver["name"]},
            "issue_date": dispatch.issue_date.isoformat(),
            "totals": {"total": totals["total"]},
            "lines": [{"name": lines[0].description}],
            "ted_timestamp": timestamp,
        }

    @api.model
    def _append_id_doc(self, header, dispatch, folio):
        id_doc = etree.SubElement(header, f"{{{SII_NAMESPACE}}}IdDoc")
        _text(id_doc, "TipoDTE", DOCUMENT_CODE)
        _text(id_doc, "Folio", int(folio))
        _text(id_doc, "FchEmis", dispatch.issue_date.isoformat())
        _text(id_doc, "IndTraslado", dispatch.transfer_indicator)

    @api.model
    def _append_issuer(self, header, issuer):
        node = etree.SubElement(header, f"{{{SII_NAMESPACE}}}Emisor")
        _text(node, "RUTEmisor", issuer["vat"])
        _text(node, "RznSoc", issuer["name"])
        _text(node, "GiroEmis", issuer["activity"])
        _text(node, "Acteco", issuer["activity_code"])
        _optional_text(node, "DirOrigen", issuer.get("address"))
        _optional_text(node, "CmnaOrigen", issuer.get("commune"))
        _optional_text(node, "CiudadOrigen", issuer.get("city"))

    @api.model
    def _append_receiver(self, header, receiver, transport=None):
        node = etree.SubElement(header, f"{{{SII_NAMESPACE}}}Receptor")
        _text(node, "RUTRecep", receiver["vat"])
        _text(node, "RznSocRecep", receiver["name"])
        _optional_text(node, "GiroRecep", receiver.get("activity"))
        _optional_text(node, "DirRecep", receiver.get("address"))
        _optional_text(node, "CmnaRecep", receiver.get("commune"))
        _optional_text(node, "CiudadRecep", receiver.get("city"))
        if transport:
            self._append_transport(node, transport)

    @api.model
    def _append_totals(self, header, lines):
        totals = self._compute_totals(lines)
        node = etree.SubElement(header, f"{{{SII_NAMESPACE}}}Totales")
        if totals["net"]:
            _text(node, "MntNeto", totals["net"])
        if totals["exempt"]:
            _text(node, "MntExe", totals["exempt"])
        if totals["net"]:
            _text(node, "TasaIVA", VAT_RATE_PERCENT)
            _text(node, "IVA", totals["vat"])
        _text(node, "MntTotal", totals["total"])

    @api.model
    def _compute_totals(self, lines):
        """Sum taxable lines as net and exempt lines as exempt.

        Unvalued lines add nothing to the totals.
        """
        net = sum(
            self._line_amount(line) for line in lines if line.tax_category == TAXABLE
        )
        exempt = sum(
            self._line_amount(line) for line in lines if line.tax_category == EXEMPT
        )
        vat = round(net * VAT_RATE_PERCENT / 100)
        return {"net": net, "exempt": exempt, "vat": vat, "total": net + exempt + vat}

    @api.model
    def _line_amount(self, line):
        return round(line.quantity * line.unit_price)

    @api.model
    def _append_transport(self, receiver_node, transport):
        node = etree.SubElement(receiver_node, f"{{{SII_NAMESPACE}}}Transporte")
        _optional_text(node, "Patente", transport.get("vehicle_plate"))
        _optional_text(node, "RUTTrans", transport.get("carrier_vat"))
        _optional_text(node, "DirDest", transport.get("destination_address"))

    @api.model
    def _append_line(self, document, index, line):
        node = etree.SubElement(document, f"{{{SII_NAMESPACE}}}Detalle")
        _text(node, "NroLinDet", index)
        if line.tax_category == EXEMPT:
            _text(node, "IndExe", EXEMPT_INDICATOR)
        elif line.tax_category == UNVALUED:
            _text(node, "IndExe", UNVALUED_INDICATOR)
        _text(node, "NmbItem", line.description)
        _text(node, "QtyItem", line.quantity)
        if line.tax_category != UNVALUED:
            _text(node, "PrcItem", line.unit_price)
        _text(
            node,
            "MontoItem",
            0 if line.tax_category == UNVALUED else self._line_amount(line),
        )
