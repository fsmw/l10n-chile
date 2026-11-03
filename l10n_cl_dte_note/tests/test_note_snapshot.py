from odoo.exceptions import UserError

from odoo.addons.l10n_cl_dte_note.services.note_snapshot import NoteSnapshotService
from odoo.addons.l10n_cl_dte_note.tests.common import DteNoteTestCase


class TestNoteSnapshot(DteNoteTestCase):
    def _full_credit_snapshot(self):
        source, target, source_snapshot = self.create_accepted_source()
        self.validate_authorization(document_code="61", folio_start=740, folio_end=745)
        note = self.create_credit_note(source, target)
        note.action_post()
        snapshot = NoteSnapshotService().capture_note(
            note,
            self.env["l10n_cl.caf.authorization"].search(
                [("document_code", "=", "61")], limit=1
            ),
            740,
            "61",
        )
        return source, target, note, source_snapshot, snapshot

    def test_note_snapshot_uses_note_economics(self):
        _source, _target, note, _source_snapshot, snapshot = (
            self._full_credit_snapshot()
        )
        self.assertEqual(snapshot["document_code"], "61")
        self.assertEqual(snapshot["totals"]["total"], str(int(note.amount_total)))
        self.assertEqual(snapshot["reference"]["reason"], "Corrección sintética")
        self.assertEqual(snapshot["adjustment"]["effect"], "credit")

    def test_credit_adjustment_within_source_total(self):
        _source, _target, note, source_snapshot, snapshot = self._full_credit_snapshot()
        source_total = int(source_snapshot["totals"]["total"])
        self.assertLessEqual(int(snapshot["totals"]["total"]), source_total)
        self.assertEqual(int(snapshot["totals"]["total"]), int(note.amount_total))

    def test_note_snapshot_immutable_after_source_change(self):
        _source, target, _note, source_snapshot, _snapshot = (
            self._full_credit_snapshot()
        )
        line = target.artifact_ids.filtered(lambda item: item.role == "source_snapshot")
        self.assertEqual(target.payload_digest, line.sha256)
        # Read through the public service after source accounting data changes.
        source_move = target.source_move_id
        source_line = source_move.invoice_line_ids.filtered(
            lambda item: item.display_type == "product"
        )[:1]
        with self.assertRaisesRegex(
            UserError, "Fiscal accounting details are immutable"
        ):
            source_line.with_context(check_move_validity=False).write(
                {"name": "Changed after DTE issue"}
            )
        from odoo.addons.l10n_cl_dte_note.services.note_reference_service import (
            NoteReferenceService,
        )

        observed = NoteReferenceService(self.env).read_original_snapshot(target)
        self.assertEqual(observed, source_snapshot)
        self.assertEqual(source_line.name, observed["lines"][0]["description"])
