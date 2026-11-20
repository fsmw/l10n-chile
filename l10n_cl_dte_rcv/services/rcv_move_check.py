# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Tell, for the journal entries of a period, whether the RCV reports them."""

from collections import defaultdict
from datetime import date

from dateutil.relativedelta import relativedelta

from .rcv_candidates import (
    EXPORT_DOCUMENT_CODE,
    is_generic_export,
    normalize_folio,
    partner_vat,
)
from .rcv_values import normalize_vat

# Every SII state of the operation must be read completely before an entry
# can be reported as absent from the RCV.
REQUIRED_SECTION_STATES = {
    "purchase": frozenset({"REGISTRO", "RECLAMADO", "PENDIENTE"}),
    "sale": frozenset({"REGISTRO"}),
}
CHECKED_MOVE_TYPES = {
    "purchase": ("in_invoice", "in_refund"),
    "sale": ("out_invoice", "out_refund"),
}
# Receipts are reported as daily totals, not as documents.
RECEIPT_DOCUMENT_CODES = ("39", "41")


def fully_covered(sync):
    """Tell whether ``sync`` read every required SII state completely."""
    required = REQUIRED_SECTION_STATES.get(sync.operation)
    complete = {
        section.sii_state for section in sync.section_ids if section.state == "complete"
    }
    return bool(required) and required <= complete


class RcvMoveCheck:
    """Mark the journal entries of a read period as observed or not in the RCV."""

    def __init__(self, env):
        self.env = env

    def check(self, sync):
        """Write the RCV state of the period entries; return the checked entries."""
        moves = self.env["account.move"]
        if sync.operation not in CHECKED_MOVE_TYPES or not moves.has_access("write"):
            return moves
        moves = self._period_moves(sync).filtered(
            lambda move: move.l10n_latam_document_type_id.code
            and move.l10n_latam_document_type_id.code not in RECEIPT_DOCUMENT_CODES
        )
        observed = self._observed(sync, moves)
        covered = fully_covered(sync)
        by_state = defaultdict(lambda: self.env["account.move"])
        for move in moves:
            if move in observed:
                state = "matched"
            else:
                state = "not_in_sii" if covered else "indeterminate"
            by_state[state] |= move
        for state, state_moves in by_state.items():
            state_moves.write({"dte_rcv_state": state, "dte_rcv_sync_id": sync.id})
        return moves

    def _period_moves(self, sync):
        period = sync.period_id
        start = date(period.year, period.month, 1)
        stop = start + relativedelta(months=1)
        return self.env["account.move"].search(
            [
                ("company_id", "=", sync.company_id.id),
                ("move_type", "in", CHECKED_MOVE_TYPES[sync.operation]),
                ("state", "!=", "cancel"),
                "|",
                "&",
                ("invoice_date", ">=", start),
                ("invoice_date", "<", stop),
                "&",
                ("date", ">=", start),
                ("date", "<", stop),
            ]
        )

    def _observed(self, sync, moves):
        """Return the entries that an RCV document links or identifies."""
        numbers = {move.l10n_latam_document_number or "" for move in moves}
        folios = numbers | {normalize_folio(number) for number in numbers}
        documents = self.env["l10n_cl.dte.rcv.document"].search(
            [
                ("company_id", "=", sync.company_id.id),
                ("operation", "=", sync.operation),
                "|",
                ("move_id", "in", moves.ids),
                ("folio", "in", sorted(folios)),
            ]
        )
        keys = {
            (doc.document_type_code, partner_vat(doc), normalize_folio(doc.folio))
            for doc in documents
        }
        export_folios = {
            normalize_folio(doc.folio) for doc in documents if is_generic_export(doc)
        }
        observed = documents.move_id
        for move in moves - observed:
            code = move.l10n_latam_document_type_id.code
            folio = normalize_folio(move.l10n_latam_document_number)
            vat = normalize_vat(move.commercial_partner_id.vat)
            if (code, vat, folio) in keys or (
                code == EXPORT_DOCUMENT_CODE
                and move.move_type == "out_invoice"
                and folio in export_folios
            ):
                observed |= move
        return observed
