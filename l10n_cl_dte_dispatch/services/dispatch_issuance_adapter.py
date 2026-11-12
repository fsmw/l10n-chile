# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from odoo import _, fields
from odoo.exceptions import UserError
from odoo.modules import get_module_path

from odoo.addons.l10n_cl_dte_core.tools.xml_safety import (
    safe_fromstring,
    safe_xml_schema,
)
from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.services.ted_composer import DteTedComposer

DOCUMENT_CODE = "52"
CHILE_TIMEZONE = ZoneInfo("America/Santiago")
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S"
ARTIFACT_PRODUCER_VERSION = "l10n_cl_dte_dispatch/18.0"
SNAPSHOT_ROLE = "source_snapshot"


class DispatchIssuanceAdapter:
    """Issuance contract for DTE 52 guides, used by DteIssuanceService.

    The service calls these methods; the adapter does not inherit from the
    account adapter, so the dispatch addon does not depend on account.
    """

    artifact_producer_version = ARTIFACT_PRODUCER_VERSION
    snapshot_producer_version = ARTIFACT_PRODUCER_VERSION

    def __init__(self, composer):
        self.composer = composer

    def source_key(self, source, document_code):
        return f"l10n_cl.dte.dispatch:{source.id}:type-{document_code}"

    def document_source_values(self, source):
        return {"source_dispatch_id": source.id}

    def prepare_document_values(self, source, document_code):
        return {}

    def validate_source(self, source, document_code):
        if document_code != DOCUMENT_CODE:
            raise UserError(_("A dispatch guide is issued only as DTE 52."))
        if source.state != "frozen":
            raise UserError(_("Freeze the dispatch snapshot before issuing its DTE."))
        if not source.partner_id.vat:
            raise UserError(_("The receiver needs a tax identifier (RUT)."))
        company = source.company_id
        if not company.partner_id.vat or not company.dte_sii_activity_code:
            raise UserError(
                _("The company needs a tax identifier and an activity code.")
            )

    def select_caf(self, source, environment, document_code):
        domain = [
            ("company_id", "=", source.company_id.id),
            ("document_code", "=", document_code),
            ("sii_environment", "=", environment),
            ("state", "=", "validated"),
            "|",
            ("date_to", "=", False),
            ("date_to", ">=", fields.Date.context_today(source)),
        ]
        caf = (
            source.env["l10n_cl.caf.authorization"]
            .sudo()
            .search(domain, order="id", limit=1)
        )
        if not caf:
            raise UserError(
                _("No validated CAF is available for DTE 52 in this environment.")
            )
        return caf

    def create_artifact(self, source, document, role, content, producer_version):
        is_snapshot = role == SNAPSHOT_ROLE
        return (
            source.env["l10n_cl.electronic.artifact"]
            .sudo()
            .create(
                {
                    "document_id": document.id,
                    "role": role,
                    "content": base64.b64encode(content),
                    "mimetype": "application/json"
                    if is_snapshot
                    else "application/xml",
                    "encoding": "UTF-8" if is_snapshot else "ISO-8859-1",
                    "producer_version": producer_version,
                }
            )
        )

    def capture_snapshot(self, source, caf, folio_number, document_code):
        timestamp = datetime.now(CHILE_TIMEZONE).strftime(TIMESTAMP_FORMAT)
        return self.composer.build_snapshot(
            source,
            folio_number,
            self._issuer(source.company_id),
            self._receiver(source.partner_id),
            timestamp,
            transport=self._transport(source),
        )

    def compose_ted(self, snapshot, caf):
        return DteTedComposer().compose_ted(snapshot, caf)

    def compose_unsigned_document(self, snapshot):
        return self.composer.serialize_unsigned_guide(snapshot)

    def sign_document(self, company, environment, unsigned_xml):
        return SignatureService().sign_document(company, environment, unsigned_xml)

    def validate_document(self, xml_bytes):
        xsd = Path(get_module_path("l10n_cl_dte_xml")) / "static/xsd/DTE_v10.xsd"
        safe_xml_schema(str(xsd)).assertValid(safe_fromstring(xml_bytes))

    def _issuer(self, company):
        partner = company.partner_id
        return {
            "vat": partner.vat,
            "name": partner.name,
            "activity": getattr(company, "l10n_cl_activity_description", "") or "",
            "activity_code": company.dte_sii_activity_code,
            "address": partner.street or "",
            "commune": partner.city or "",
            "city": partner.city or "",
        }

    def _receiver(self, partner):
        return {
            "vat": partner.vat,
            "name": partner.name,
            "activity": "",
            "address": partner.street or "",
            "commune": partner.city or "",
            "city": partner.city or "",
        }

    def _transport(self, source):
        """Return the transport block only when the guide names a vehicle or carrier."""
        if not (source.vehicle_plate or source.carrier_id.vat):
            return {}
        return {
            key: value
            for key, value in {
                "vehicle_plate": source.vehicle_plate,
                "carrier_vat": source.carrier_id.vat,
                "destination_address": source.destination_address,
            }.items()
            if value
        }
