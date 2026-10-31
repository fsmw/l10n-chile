# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""SII PDF417 timbre rendering shared by invoice, guide, POS and export reports."""

import base64
import io

from lxml import etree

from odoo import _
from odoo.exceptions import UserError

try:
    import pdf417gen
except ImportError:  # pragma: no cover
    pdf417gen = None

SECURITY_LEVEL = 5
COLUMNS = 13


def pdf417_available():
    """Return whether the optional pdf417gen dependency can be imported."""
    return pdf417gen is not None


def _pdf417gen_or_raise():
    if pdf417gen is None:
        raise UserError(
            _("Instale la dependencia pdf417gen para imprimir el timbre SII.")
        )
    return pdf417gen


def ted_payload(ted_element):
    """Serialize a TED element the way the SII expects it.

    Local-name tags avoid leaking the SiiDte namespace into the barcode and the
    ISO-8859-1 bytes match the signed document.
    """
    for node in ted_element.iter():
        node.tag = etree.QName(node).localname
    etree.cleanup_namespaces(ted_element)
    return etree.tostring(ted_element, encoding="ISO-8859-1", xml_declaration=False)


def render_pdf417(payload, *, padding=15, scale=1):
    """Return the base64 PNG for an already serialized timbre payload."""
    library = _pdf417gen_or_raise()
    barcode = library.render_image(
        library.encode(payload, security_level=SECURITY_LEVEL, columns=COLUMNS),
        padding=padding,
        scale=scale,
    )
    output = io.BytesIO()
    barcode.save(output, "PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def render_ted_timbre(ted_element, *, padding=15, scale=1):
    """Return the base64 PNG of the SII PDF417 timbre for a TED element."""
    return render_pdf417(ted_payload(ted_element), padding=padding, scale=scale)
