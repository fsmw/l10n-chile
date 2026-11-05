# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import importlib
import io

from lxml import etree
from PIL import Image

from odoo import _, models
from odoo.exceptions import ValidationError

from ..models.report_sample import _extract_ted_bytes

# pdf417gen 0.8.1 uses Pillow's Resampling enum, while Odoo 18's supported
# Python 3.10 image pin predates it. Provide the equivalent legacy constants
# only while importing pdf417gen, then restore Pillow's original module API.
_HAS_RESAMPLING = hasattr(Image, "Resampling")
if not _HAS_RESAMPLING:
    Image.Resampling = Image
try:
    _pdf417gen = importlib.import_module("pdf417gen")
finally:
    if not _HAS_RESAMPLING:
        delattr(Image, "Resampling")

encode = _pdf417gen.encode
render_image = _pdf417gen.render_image


def _find_local_text(element, local_name):
    matches = [
        child
        for child in element.iter()
        if etree.QName(child).localname == local_name and child.text
    ]
    return matches[0].text.strip() if len(matches) == 1 else ""


class ReportDtePrintSample(models.AbstractModel):
    _name = "report.l10n_cl_dte_report.dte_print_sample"
    _description = "Chilean DTE Print Sample"

    def _get_report_values(self, docids, data=None):
        samples = self.env["l10n_cl.dte.report.sample"].browse(docids).exists()
        if len(samples) != len(docids):
            raise ValidationError(_("One or more print samples no longer exist."))
        output = {}
        for sample in samples:
            content = sample._get_signed_xml()
            ted = _extract_ted_bytes(content)
            try:
                root = etree.fromstring(
                    content,
                    etree.XMLParser(
                        resolve_entities=False, no_network=True, load_dtd=False
                    ),
                )
                image = render_image(
                    encode(ted.decode("ascii"), columns=18, security_level=4),
                    scale=3,
                    ratio=3,
                    padding=8,
                )
                buffer = io.BytesIO()
                image.save(buffer, format="PNG", optimize=False)
            except (ValueError, OSError) as error:
                raise ValidationError(
                    _("The TED could not be rendered as a PDF417 barcode.")
                ) from error
            document = next(
                (
                    element
                    for element in root.iter()
                    if etree.QName(element).localname == "Documento"
                ),
                None,
            )
            header = next(
                (
                    element
                    for element in root.iter()
                    if etree.QName(element).localname == "Encabezado"
                ),
                None,
            )
            if document is None or header is None:
                raise ValidationError(_("The signed DTE has no printable header."))
            output[sample.id] = {
                "barcode": base64.b64encode(buffer.getvalue()).decode("ascii"),
                "document_code": sample.document_code,
                "folio": sample.folio,
                "issue_date": _find_local_text(header, "FchEmis"),
                "issuer_vat": _find_local_text(header, "RUTEmisor"),
                "issuer_name": _find_local_text(header, "RznSoc")
                or _find_local_text(header, "RznSocEmisor"),
                "issuer_activity": _find_local_text(header, "GiroEmis")
                or _find_local_text(header, "GiroEmisor"),
                "issuer_address": _find_local_text(header, "DirOrigen"),
                "issuer_commune": _find_local_text(header, "CmnaOrigen"),
                "receiver_vat": _find_local_text(header, "RUTRecep"),
                "receiver_name": _find_local_text(header, "RznSocRecep"),
                "net": _find_local_text(header, "MntNeto"),
                "exempt": _find_local_text(header, "MntExe"),
                "vat": _find_local_text(header, "IVA"),
                "total": _find_local_text(header, "MntTotal"),
                "environment": sample.document_id.sii_environment,
                "references": [
                    {
                        "document_code": _find_local_text(reference, "TpoDocRef"),
                        "folio": _find_local_text(reference, "FolioRef"),
                        "date": _find_local_text(reference, "FchRef"),
                        "code": _find_local_text(reference, "CodRef"),
                        "reason": _find_local_text(reference, "RazonRef"),
                    }
                    for reference in document
                    if etree.QName(reference).localname == "Referencia"
                ],
                "details": [
                    {
                        "name": _find_local_text(detail, "NmbItem"),
                        "description": _find_local_text(detail, "DscItem"),
                        "quantity": _find_local_text(detail, "QtyItem"),
                        "amount": _find_local_text(detail, "MontoItem"),
                    }
                    for detail in document
                    if etree.QName(detail).localname == "Detalle"
                ],
            }
        return {
            "doc_ids": samples.ids,
            "doc_model": samples._name,
            "docs": samples,
            "sample_data": output,
        }
