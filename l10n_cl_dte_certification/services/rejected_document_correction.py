# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from lxml import etree

from odoo import _
from odoo.exceptions import UserError

from odoo.addons.l10n_cl_dte_core.tools.xml_safety import safe_fromstring
from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import (
    SII_NAMESPACE,
    DteXmlComposer,
)


class RejectedCertificationDocumentCorrection:
    """Append corrected XML only after the caller proves full SII rejection."""

    def prepare(self, line, original, sent):
        document = line.document_id
        previous = document._get_signed_artifact()
        previous.action_verify_integrity()
        if base64.b64decode(previous.content, validate=True) != original:
            raise UserError(_("The current DTE differs from its retained evidence."))
        root = safe_fromstring(original)
        retained = safe_fromstring(etree.tostring(sent, with_tail=False))
        etree.cleanup_namespaces(root)
        etree.cleanup_namespaces(retained)
        if etree.tostring(root, method="c14n") != etree.tostring(
            retained, method="c14n"
        ):
            raise UserError(_("The current DTE differs from its retained evidence."))
        signatures = root.xpath("./*[local-name()='Signature']")
        if len(signatures) != 1:
            raise UserError(_("A signed DTE needs one Documento and Signature."))
        root.remove(signatures[0])
        etree.cleanup_namespaces(root)
        prefix = f"{{{SII_NAMESPACE}}}"
        changes = []
        for detail in root.findall(prefix + "Documento/" + prefix + "Detalle"):
            percent = detail.findtext(prefix + "DescuentoPct")
            if percent is None or detail.find(prefix + "DescuentoMonto") is not None:
                continue
            amount = DteXmlComposer.line_discount_amount(
                detail.findtext(prefix + "QtyItem"),
                detail.findtext(prefix + "PrcItem"),
                percent,
            )
            node = etree.Element(prefix + "DescuentoMonto")
            node.text = str(amount)
            detail.insert(
                list(detail).index(detail.find(prefix + "DescuentoPct")) + 1, node
            )
            changes.append(
                {
                    "line": detail.findtext(prefix + "NroLinDet"),
                    "discount_amount": str(amount),
                }
            )
        corrected = SignatureService().sign_document(
            document.company_id,
            "certification",
            etree.tostring(root, encoding="ISO-8859-1", xml_declaration=True),
        )
        DteXmlComposer().validate_document(corrected, strict_sii=True)
        replacement = document.env[
            "l10n_cl.electronic.artifact"
        ]._create_signature_revision(
            previous,
            corrected,
            "l10n_cl_dte_certification/rejected-document-correction/18.0",
        )
        return corrected, {
            "document_id": document.id,
            "previous_artifact_id": previous.id,
            "replacement_artifact_id": replacement.id,
            "previous_signature_digest": previous.sha256,
            "replacement_signature_digest": replacement.sha256,
            "detail_changes": changes,
        }
