# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from lxml import etree
from odoo import _
from odoo.exceptions import ValidationError


def parse_incoming_dte(content, translate=None):
    """Extract the identity fields from one incoming DTE without trusting XML."""
    translate = translate or _
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        huge_tree=False,
        remove_comments=True,
    )
    try:
        root = etree.fromstring(content, parser=parser)
    except (etree.XMLSyntaxError, TypeError, ValueError) as error:
        raise ValidationError(
            translate("The incoming DTE is not well-formed XML.")
        ) from error
    dtes = root.xpath("//*[local-name()='DTE']")
    if len(dtes) != 1:
        raise ValidationError(
            translate("Import exactly one DTE per incoming document.")
        )
    dte = dtes[0]
    headers = dte.xpath("./*[local-name()='Documento']/*[local-name()='Encabezado']")
    if len(headers) != 1:
        raise ValidationError(
            translate("The DTE must contain exactly one document header.")
        )
    header = headers[0]

    def read_text(path):
        values = header.xpath(path)
        return values[0].text.strip() if values and values[0].text else False

    document_code = read_text("./*[local-name()='IdDoc']/*[local-name()='TipoDTE']")
    folio = read_text("./*[local-name()='IdDoc']/*[local-name()='Folio']")
    issue_date = read_text("./*[local-name()='IdDoc']/*[local-name()='FchEmis']")
    issuer_vat = read_text("./*[local-name()='Emisor']/*[local-name()='RUTEmisor']")
    receiver_vat = read_text("./*[local-name()='Receptor']/*[local-name()='RUTRecep']")
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
    return {
        "document_code": str(code_number),
        "folio": folio_number,
        "issue_date": issue_date,
        "issuer_vat": issuer_vat,
        "receiver_vat": receiver_vat,
    }
