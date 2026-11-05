# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.addons.l10n_cl_dte_boleta.services.boleta_issuance_adapter import (
    BoletaIssuanceAdapter,
)
from odoo.addons.l10n_cl_dte_pos.services.pos_boleta_issuance_adapter import (
    PosBoletaIssuanceAdapter,
)

from .pos_line_mapping import PosLineMappingService


class PosTrackedBoletaIssuanceAdapter(PosBoletaIssuanceAdapter):
    def capture_snapshot(self, source, caf, folio_number, document_code):
        snapshot = super().capture_snapshot(source, caf, folio_number, document_code)
        return PosLineMappingService(source.env).attach_snapshot_lineage(
            snapshot, source
        )


class PosTrackedInvoiceBoletaIssuanceAdapter(BoletaIssuanceAdapter):
    def capture_snapshot(self, source, caf, folio_number, document_code):
        snapshot = super().capture_snapshot(source, caf, folio_number, document_code)
        return PosLineMappingService(source.env).attach_snapshot_lineage(
            snapshot, source.pos_order_ids, move=source
        )
