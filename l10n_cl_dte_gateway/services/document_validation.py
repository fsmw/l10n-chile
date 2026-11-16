# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from lxml import etree

from odoo.exceptions import ValidationError
from odoo.tools.translate import LazyTranslate

from odoo.addons.l10n_cl_dte_core.tools.dte_payload import select_dte_payload
from odoo.addons.l10n_cl_dte_core.tools.xml_safety import safe_fromstring
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import DteXmlComposer

_lt = LazyTranslate(__name__, default_lang="en_US")
_CODE_PATH = "{http://www.sii.cl/SiiDte}Encabezado/{http://www.sii.cl/SiiDte}IdDoc/{http://www.sii.cl/SiiDte}TipoDTE"


def get_document_validator(document_code):
    """Keep export validation closed until a dedicated extension supplies it."""
    if document_code not in {"33", "34", "39", "41", "46", "52", "56", "61"}:
        raise ValidationError(
            _lt("No document validator is configured for this DTE type.")
        )
    return DteXmlComposer()


def validate_document_payload(
    content, validator_getter, *, allowed_codes=None, strict_sii=False
):
    """Select before dispatch; validate the original bytes and return the payload."""
    try:
        payload = select_dte_payload(
            safe_fromstring(content), allowed_codes=allowed_codes
        )
    except (ValueError, etree.XMLSyntaxError) as error:
        raise ValidationError(
            _lt("The DTE fiscal payload is invalid or ambiguous.")
        ) from error
    options = {"strict_sii": True} if strict_sii else {}
    validator_getter(payload.findtext(_CODE_PATH)).validate_document(content, **options)
    return payload
