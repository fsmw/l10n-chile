# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo.exceptions import UserError
from odoo.tests import tagged

from ..services.export_xml_composer import ExportXmlComposer
from .common import ExportNoteCase


@tagged("post_install", "-at_install")
class TestExportAdjustment(ExportNoteCase):
    """The Export Note wizard prepares a draft 111/112 on an accepted 110."""

    def _wizard(self, **values):
        return (
            self.env["l10n_cl.dte.export.adjustment.wizard"]
            .with_user(self.dte_manager)
            .create(
                {
                    "move_id": self.root.source_move_id.id,
                    "reason": "Wrong buyer address",
                    **values,
                }
            )
        )

    def _draft(self, **values):
        action = self._wizard(**values).action_create_note()
        return self.env["account.move"].browse(action["res_id"])

    def test_text_correction_is_a_zero_amount_112(self):
        note = self._draft(correction="2")
        self.assertEqual(note.move_type, "out_refund")
        self.assertEqual(note.dte_export_target_id, self.root)
        self.assertEqual(note.amount_total, 0)
        note.action_post()
        document = self._issue(note)
        signed = base64.b64decode(document.sudo()._get_signed_artifact().content)
        self.assertTrue(ExportXmlComposer().validate_document(signed))
        self.assertNotIn(b"<PrcItem>", signed)
        self.assertNotIn(b"<OtraMoneda>", signed)

    def test_cancellation_copies_the_whole_export(self):
        note = self._draft(correction="1")
        self.assertEqual(note.amount_total, self.root.source_move_id.amount_total)
        self.assertEqual(note.l10n_latam_document_type_id.code, "112")

    def test_debit_note_only_corrects_amounts(self):
        with self.assertRaises(UserError):
            self._draft(document_code="111", correction="1")
        note = self._draft(document_code="111")
        self.assertEqual(note.move_type, "out_invoice")
