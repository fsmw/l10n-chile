# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Golden-file tests for notes 61 and 56 that reference an accepted 33.

Each test issues the note through the real flow and compares the signed XML
with a reviewed file in ``tests/golden/``. See
``l10n_cl_dte_xml/tests/golden.py`` for masking and regeneration.
"""

import base64
from pathlib import Path

from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_xml.tests.golden import (
    assert_golden_xml,
    frozen_chile_now,
)

from .common import DteNoteTestCase

GOLDEN_DIR = Path(__file__).parent / "golden"
NOTE_SNAPSHOT_MODULE = "odoo.addons.l10n_cl_dte_note.services.note_snapshot"


@tagged("post_install", "-at_install")
class TestNoteGoldenXml(DteNoteTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # SII requires GiroRecep on 33, 34 and 56; the generator emits it
        # from the receiver's activity description.
        cls.receiver.sudo().l10n_cl_activity_description = "Comercio al por menor"

    def issue(self, note, code, folio):
        self.validate_authorization(
            document_code=code, folio_start=folio, folio_end=folio + 5
        )
        self.create_signing_profile()
        with frozen_chile_now(NOTE_SNAPSHOT_MODULE):
            document = note.with_user(self.dte_manager).issue_dte()
        return base64.b64decode(document._get_signed_artifact().content)

    def test_credit_note_61_cancels_accepted_invoice_33(self):
        source, target, _snapshot = self.create_accepted_source("33")
        note = self.create_credit_note(source, target, operation="cancel")
        note.action_post()
        assert_golden_xml(
            self, self.issue(note, "61", 780), GOLDEN_DIR / "dte_61_cancel_33.xml"
        )

    def test_debit_note_56_increases_amount_of_accepted_invoice_33(self):
        source, target, _snapshot = self.create_accepted_source("33")
        note = self.make_invoice("56", post=False)
        note.write(
            {
                **source._prepare_dte_note_values(target, "amount", "Aumento de monto"),
                "debit_origin_id": source.id,
                "l10n_latam_document_type_id": self.document_types["56"].id,
            }
        )
        note.invoice_line_ids.write({"dte_note_origin_line_number": 1})
        note.action_post()
        assert_golden_xml(
            self, self.issue(note, "56", 790), GOLDEN_DIR / "dte_56_amount_33.xml"
        )
