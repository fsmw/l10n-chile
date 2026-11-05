# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_boleta.services.boleta_issuance_adapter import (
    BoletaIssuanceAdapter,
)


class PosBoletaIssuanceAdapter(BoletaIssuanceAdapter):
    """Adapt a paid POS order to the shared DTE issuance protocol."""

    artifact_producer_version = "l10n_cl_dte_pos/18.0"
    snapshot_producer_version = "l10n_cl_dte_pos/18.0"

    def __init__(self):
        super().__init__()
        from .pos_boleta_snapshot import PosBoletaSnapshotService

        self.pos_snapshot_service = PosBoletaSnapshotService()

    def source_key(self, source, document_code):
        """Return the stable company-scoped POS source key."""
        if source._name != "pos.order":
            raise ValidationError(_("The POS DTE adapter requires a POS order."))
        return f"pos.order:{source.id}:type-{document_code}"

    def document_source_values(self, source):
        """Return electronic-document relations for a POS order source."""
        if source._name != "pos.order":
            raise ValidationError(_("The POS DTE adapter requires a POS order."))
        return {"source_pos_order_id": source.id}

    def validate_source(self, source, document_code):
        """Validate paid order, company, session policy, and boleta type."""
        return self.pos_snapshot_service.validate_source(source, document_code)

    def capture_snapshot(self, source, caf, folio_number, document_code):
        """Capture the immutable fiscal snapshot from the POS order."""
        return self.pos_snapshot_service.capture(
            source, caf, folio_number, document_code
        )

    def select_caf(self, source, environment, document_code):
        """Select a CAF valid for this company, environment, and DTE type."""
        selected_id = source.env.context.get("dte_caf_authorization_id")
        domain = [
            ("company_id", "=", source.company_id.id),
            ("document_code", "=", document_code),
            ("sii_environment", "=", environment),
            ("state", "=", "validated"),
            "|",
            ("date_to", "=", False),
            ("date_to", ">=", source.date_order.date()),
        ]
        if selected_id:
            domain.append(("id", "=", selected_id))
        cafs = source.env["l10n_cl.caf.authorization"].search(domain)
        if len(cafs) != 1:
            raise ValidationError(
                _("Select exactly one current validated CAF for DTE type %s.")
                % document_code
            )
        cafs._check_caf_integrity()
        return cafs

    def create_artifact(self, source, document, role, content, producer_version):
        """Persist an immutable DTE artifact for this source."""
        is_snapshot = role == "source_snapshot"
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
