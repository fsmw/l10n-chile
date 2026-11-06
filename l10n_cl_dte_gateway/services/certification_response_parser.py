# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import re

from lxml import etree

from odoo import _
from odoo.exceptions import ValidationError


def parse_xml(content):
    """Read bounded external XML without DTDs, entities or network resolution."""
    if not isinstance(content, bytes) or not content or len(content) > 2_000_000:
        raise ValidationError(
            _("The SII XML response is empty or exceeds the allowed size.")
        )
    try:
        root = etree.fromstring(
            content,
            etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True),
        )
    except etree.XMLSyntaxError as error:
        raise ValidationError(_("The SII response is not valid XML.")) from error
    if root.getroottree().docinfo.doctype:
        raise ValidationError(
            _("SII XML responses must not contain a document type declaration.")
        )
    return root


def text(root, name, required=True):
    values = root.xpath(".//*[local-name()=$name]/text()", name=name)
    if len(values) != 1 or not values[0].strip():
        if not required and not values:
            return ""
        raise ValidationError(
            _("The SII response has a missing or ambiguous %(field)s.")
            % {"field": name}
        )
    return values[0].strip()


def soap_payload(content, operation):
    root = parse_xml(content)
    if root.tag != "{http://schemas.xmlsoap.org/soap/envelope/}Envelope":
        raise ValidationError(_("SII returned an unexpected SOAP envelope."))
    if root.xpath(".//*[local-name()='Fault']"):
        raise ValidationError(
            _("SII returned a SOAP fault. The operation was not confirmed.")
        )
    # SOAP has already decoded the inner XML string to Unicode. Its original
    # byte-encoding declaration must not reinterpret the new UTF-8 bytes.
    payload = re.sub(
        r"(<\?xml[^>]*encoding\s*=\s*)[\"'][^\"']+[\"']",
        r'\1"UTF-8"',
        text(root, operation + "Return"),
        count=1,
    )
    return parse_xml(payload.encode("UTF-8"))
