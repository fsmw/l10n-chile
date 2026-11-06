# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib

from lxml import etree

from odoo import _, models
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_xml.services.xml_composer import SII_NAMESPACE

from ..services.note_reference_service import NoteReferenceService
from ..services.note_xml_composer import NoteXmlComposer


class ElectronicSubmission(models.Model):
    _inherit = "l10n_cl.electronic.submission"

    def _validate_document_payload(self, request_bytes):
        """Verify note signatures and match request identity to its frozen snapshot."""
        self.ensure_one()
        document = self.document_id
        if document.document_code not in {"61", "56"}:
            return super()._validate_document_payload(request_bytes)
        NoteXmlComposer().validate_document(request_bytes)
        signed_artifacts = document._get_signed_artifact()
        if len(signed_artifacts) != 1:
            raise ValidationError(
                _("A DTE note submission requires exactly one signed XML artifact.")
            )
        signed_artifacts.action_verify_integrity()
        signed_bytes = base64.b64decode(signed_artifacts.content, validate=True)
        if (
            hashlib.sha256(request_bytes).hexdigest()
            != hashlib.sha256(signed_bytes).hexdigest()
        ):
            raise ValidationError(
                _("The DTE note request differs from its signed XML evidence.")
            )
        snapshot = NoteReferenceService(self.env).read_original_snapshot(document)
        try:
            root = etree.fromstring(
                request_bytes,
                parser=etree.XMLParser(
                    resolve_entities=False, no_network=True, load_dtd=False
                ),
            )
        except etree.XMLSyntaxError as error:
            raise ValidationError(
                _("The DTE note request XML is malformed.")
            ) from error
        dte = root.find(f"{{{SII_NAMESPACE}}}Documento")
        reference = (
            dte.find(f"{{{SII_NAMESPACE}}}Referencia") if dte is not None else None
        )
        header = dte.find(f"{{{SII_NAMESPACE}}}Encabezado") if dte is not None else None

        def text(parent, name):
            path = "/".join(f"{{{SII_NAMESPACE}}}{part}" for part in name.split("/"))
            item = parent.find(path) if parent is not None else None
            return item.text if item is not None else None

        expected = snapshot["reference"]
        if (
            text(header, "IdDoc/TipoDTE") != str(document.document_code)
            or text(header, "IdDoc/Folio") != str(document.folio)
            or text(header, "Emisor/RUTEmisor") != snapshot["issuer"]["vat"]
            or text(header, "Receptor/RUTRecep") != snapshot["receiver"]["vat"]
            or text(header, "Totales/MntTotal") != str(snapshot["totals"]["total"])
            or text(reference, "TpoDocRef") != str(expected["document_code"])
            or text(reference, "FolioRef") != str(expected["folio"])
            or text(reference, "FchRef") != expected["issue_date"]
            or text(reference, "CodRef") != str(expected["reference_code"])
            or text(reference, "RazonRef") != expected["reason"]
        ):
            raise ValidationError(
                _("The signed DTE note does not match its frozen fiscal snapshot.")
            )
        return True
