# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.addons.l10n_cl_dte_note.services.note_issuance_adapter import (
    NoteIssuanceAdapter,
)
from odoo.addons.l10n_cl_dte_note.services.note_xml_composer import NoteXmlComposer
from odoo.addons.l10n_cl_dte_pos.services.pos_boleta_issuance_adapter import (
    PosBoletaIssuanceAdapter,
)

from .pos_return_policy import PosReturnPolicyService
from .pos_return_snapshot import (
    PosAccountingNoteSnapshotService,
    PosReturnSnapshotService,
)


class PosReturnIssuanceAdapter(PosBoletaIssuanceAdapter):
    supported_document_codes = frozenset({"61"})
    snapshot_producer_version = "l10n_cl_dte_pos_return/18.0"
    artifact_producer_version = "l10n_cl_dte_pos_return/18.0"

    def __init__(self):
        super().__init__()
        self.xml_composer = NoteXmlComposer()
        self.pos_snapshot_service = PosReturnSnapshotService()

    def validate_source(self, source, document_code):
        PosReturnPolicyService().validate_return(source, lock=True)
        return self.pos_snapshot_service.validate_source(source, document_code)

    def prepare_document_values(self, source, document_code):
        return {
            "note_target_document_id": source.dte_return_target_document_id.id,
            "note_root_document_id": source.dte_return_target_document_id.id,
            "note_pos_return_order_id": source.id,
        }


class PosAccountingNoteIssuanceAdapter(NoteIssuanceAdapter):
    supported_document_codes = frozenset({"61"})
    snapshot_producer_version = "l10n_cl_dte_pos_return/18.0"
    artifact_producer_version = "l10n_cl_dte_pos_return/18.0"

    def __init__(self):
        super().__init__()
        self.snapshot_service = PosAccountingNoteSnapshotService()

    def validate_source(self, source, document_code):
        order = PosReturnPolicyService().authorize_accounting_source(source)
        PosReturnPolicyService().validate_return(order, lock=True)
        return self.snapshot_service.validate_note_source(source, document_code)

    def prepare_document_values(self, source, document_code):
        return {
            **super().prepare_document_values(source, document_code),
            "note_pos_return_order_id": source.dte_pos_return_order_id.id,
        }
