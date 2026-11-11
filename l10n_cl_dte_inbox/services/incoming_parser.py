# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import date

from lxml import etree
from odoo import _
from odoo.addons.l10n_cl_dte_core.tools.xml_safety import safe_fromstring
from odoo.exceptions import ValidationError

SII_NS = "http://www.sii.cl/SiiDte"
MAX_XML_BYTES = 20 * 1024 * 1024
MAX_DOCUMENTS = 2000


def _read_xml(content, translate):
    if not isinstance(content, bytes) or not content:
        raise ValidationError(translate("The incoming DTE XML is empty."))
    if len(content) > MAX_XML_BYTES:
        raise ValidationError(translate("The incoming XML exceeds 20 MiB."))
    try:
        root = safe_fromstring(content)
    except (etree.XMLSyntaxError, ValueError) as error:
        raise ValidationError(
            translate("The incoming DTE is not well-formed XML.")
        ) from error
    if root.getroottree().docinfo.doctype or any(
        isinstance(node, etree._Entity) for node in root.iter()
    ):
        raise ValidationError(translate("DTD and entity declarations are not allowed."))
    return root


def extract_incoming_dtes(content, translate):
    """Parse bounded XML without resolving entities or accepting foreign wrappers."""
    root = _read_xml(content, translate)
    if root.tag == f"{{{SII_NS}}}DTE":
        documents = [root]
    elif root.tag in {f"{{{SII_NS}}}EnvioDTE", f"{{{SII_NS}}}EnvioBOLETA"}:
        sets = root.findall(f"{{{SII_NS}}}SetDTE")
        if len(sets) != 1:
            raise ValidationError(translate("An incoming envelope needs one SetDTE."))
        documents = sets[0].findall(f"{{{SII_NS}}}DTE")
    else:
        raise ValidationError(translate("Use a SII DTE, EnvioDTE or EnvioBOLETA root."))
    if not documents or len(documents) > MAX_DOCUMENTS:
        raise ValidationError(translate("Import between 1 and 2000 DTEs per file."))
    if len(root.findall(f".//{{{SII_NS}}}DTE")) != (
        0 if documents == [root] else len(documents)
    ):
        raise ValidationError(translate("DTEs must be direct children of SetDTE."))
    ids = [node.get("ID") for node in root.iter() if node.get("ID")]
    if len(ids) != len(set(ids)):
        raise ValidationError(translate("XML identifiers must be unique in the file."))
    return root, documents


def parse_incoming_dte(content, translate=None):
    """Extract the identity fields from one incoming DTE without trusting XML."""
    translate = translate or _
    _root, dtes = extract_incoming_dtes(content, translate)
    if len(dtes) != 1:
        raise ValidationError(
            translate("Import exactly one DTE per incoming document.")
        )
    dte = dtes[0]
    headers = dte.findall(f"{{{SII_NS}}}Documento/{{{SII_NS}}}Encabezado")
    if len(headers) != 1:
        raise ValidationError(
            translate("The DTE must contain exactly one document header.")
        )
    header = headers[0]

    def read_text(path):
        values = header.findall(
            "/".join(f"{{{SII_NS}}}{part}" for part in path.split("/"))
        )
        return values[0].text.strip() if len(values) == 1 and values[0].text else False

    document_code = read_text("IdDoc/TipoDTE")
    folio = read_text("IdDoc/Folio")
    issue_date = read_text("IdDoc/FchEmis")
    issuer_vat = read_text("Emisor/RUTEmisor")
    receiver_vat = read_text("Receptor/RUTRecep")
    if not all((document_code, folio, issue_date, issuer_vat, receiver_vat)):
        raise ValidationError(
            translate("The DTE header is missing required identity fields.")
        )
    try:
        code_number = int(document_code)
        folio_number = int(folio)
    except ValueError as error:
        raise ValidationError(
            translate("DTE type and folio must be numeric.")
        ) from error
    if code_number <= 0 or folio_number <= 0:
        raise ValidationError(translate("DTE type and folio must be positive."))
    try:
        date.fromisoformat(issue_date)
    except ValueError as error:
        raise ValidationError(translate("The DTE issue date is invalid.")) from error
    return {
        "document_code": str(code_number),
        "folio": folio_number,
        "issue_date": issue_date,
        "issuer_vat": issuer_vat,
        "receiver_vat": receiver_vat,
    }
