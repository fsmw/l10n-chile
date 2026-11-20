# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from decimal import Decimal

from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_note.services.note_reference_service import (
    NoteReferenceService,
)

ROOT_CODE = "110"
NOTE_CODES = frozenset({"111", "112"})
CANCEL, TEXT, AMOUNTS = "1", "2", "3"


class ExportReferenceService:
    """Rules of export notes 111/112 against their accepted root 110.

    Capacity is kept in the transaction currency of the root: the accepted
    110 total, plus accepted amount debit notes, minus every amount credit
    note in any state. Pending or rejected debit notes add nothing.
    """

    def __init__(self, env):
        self.env = env

    def root(self, move):
        """Return the accepted root 110 a note refers to, or explain why not."""
        env = self.env
        root = move.dte_export_target_id.sudo()
        if not root:
            raise ValidationError(env._("Export notes need the accepted export 110."))
        if root.document_code != ROOT_CODE or root.fiscal_state != "accepted":
            raise ValidationError(
                env._("Export notes can only refer to an accepted export 110.")
            )
        if (
            root.company_id != move.company_id
            or root.sii_environment != move.company_id.dte_sii_environment
        ):
            raise ValidationError(
                env._("The export 110 belongs to another company or SII mode.")
            )
        return root

    def root_snapshot(self, root):
        return NoteReferenceService(self.env).read_original_snapshot(root)

    def validate(self, move, document_code):
        """Check the correction against the root and its remaining capacity."""
        env = self.env
        code = str(document_code)
        root = self.root(move)
        snapshot = self.root_snapshot(root)
        correction = move.dte_export_correction
        if not correction or not (move.dte_export_reason or "").strip():
            raise ValidationError(
                env._("Export notes need a correction code and a reason.")
            )
        if code == "111" and correction != AMOUNTS:
            raise ValidationError(
                env._("An export debit note can only correct amounts.")
            )
        receiver = move.partner_id.commercial_partner_id
        if receiver.id != snapshot["receiver"]["commercial_partner_id"]:
            raise ValidationError(
                env._("The export note must keep the recipient of the export.")
            )
        if move.currency_id.name != snapshot["currency"]["iso_code"]:
            raise ValidationError(
                env._("The export note must keep the currency of the export.")
            )
        if correction != AMOUNTS and move.dte_export_reference_ids:
            raise ValidationError(
                env._("Cancellations and text corrections carry no other references.")
            )
        if code == "112":
            self._validate_credit(move, root, snapshot, correction)
        return root, snapshot

    def lock_root(self, root):
        """Serialize notes of one export: concurrent writers conflict here."""
        self.env.cr.execute(
            "UPDATE l10n_cl_electronic_document"
            " SET export_revision = export_revision + 1"
            " WHERE id = %s RETURNING export_revision",
            [root.id],
        )
        root.invalidate_recordset(["export_revision"])
        return self.env.cr.fetchone()[0]

    def remaining_amount(self, root, snapshot=None):
        """Amount still available for credit notes, in the export currency."""
        snapshot = snapshot or self.root_snapshot(root)
        remaining = Decimal(snapshot["totals"]["total"])
        for note, note_snapshot in self._notes(root):
            correction = note_snapshot.get("adjustment", {}).get("correction")
            if correction == TEXT:
                continue
            amount = Decimal(note_snapshot["totals"]["total"])
            if note.document_code == "112":
                remaining -= amount
            elif note.fiscal_state == "accepted":
                remaining += amount
        return remaining

    def reference(self, move, root_snapshot):
        """The fiscal reference to the root, always the first Referencia."""
        return {
            "document_type": ROOT_CODE,
            "folio": str(root_snapshot["folio"]),
            "date": root_snapshot["issue_date"],
            "code": move.dte_export_correction,
            "reason": move.dte_export_reason.strip()[:90],
        }

    def _validate_credit(self, move, root, snapshot, correction):
        env = self.env
        total = Decimal(str(move.amount_total))
        if correction == TEXT:
            return
        if correction == CANCEL:
            if any(
                note_snapshot.get("adjustment", {}).get("correction") != TEXT
                for _note, note_snapshot in self._notes(root)
            ):
                raise ValidationError(
                    env._("An export with amount notes cannot be cancelled whole.")
                )
            root_total = Decimal(snapshot["totals"]["total"])
            if move.currency_id.compare_amounts(float(total), float(root_total)):
                raise ValidationError(
                    env._("A cancellation must credit the whole export total.")
                )
            return
        if (
            move.currency_id.compare_amounts(
                float(total), float(self.remaining_amount(root, snapshot))
            )
            > 0
        ):
            raise ValidationError(
                env._("The credit note exceeds the amount left on the export.")
            )

    def _notes(self, root):
        documents = (
            self.env["l10n_cl.electronic.document"]
            .sudo()
            .search([("export_root_document_id", "=", root.id)])
        )
        reader = NoteReferenceService(self.env)
        for document in documents:
            if not document.event_ids.filtered(
                lambda event: event.event_code == "source_snapshot_recorded"
            ):
                continue
            yield document, reader.read_original_snapshot(document)
