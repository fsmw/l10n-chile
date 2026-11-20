# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from lxml import etree

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from ..services.export_reference_service import ExportReferenceService
from ..services.export_xml_composer import ExportXmlComposer
from .common import ExportNoteCase

NS = {"sii": "http://www.sii.cl/SiiDte"}


@tagged("post_install", "-at_install")
class TestExportNotes(ExportNoteCase):
    """Export notes 111/112 refer to an accepted 110 within its capacity."""

    def test_credit_note_is_a_signed_112_referring_to_its_root(self):
        document = self._issue(self._note("112", 1000))
        self.assertEqual(document.export_root_document_id, self.root)
        signed = base64.b64decode(document.sudo()._get_signed_artifact().content)
        self.assertTrue(ExportXmlComposer().validate_document(signed))
        root = etree.fromstring(signed)
        first = root.find(".//sii:Referencia", NS)
        self.assertEqual(first.findtext("sii:TpoDocRef", namespaces=NS), "110")
        self.assertEqual(
            first.findtext("sii:FolioRef", namespaces=NS), str(self.root.folio)
        )
        self.assertEqual(first.findtext("sii:CodRef", namespaces=NS), "3")
        self.assertEqual(
            root.findtext(".//sii:Receptor/sii:RUTRecep", namespaces=NS), "55555555-5"
        )
        remaining = ExportReferenceService(self.env).remaining_amount(self.root)
        self.assertEqual(str(remaining), "1469.00")

    def test_credit_beyond_the_remaining_amount_is_refused(self):
        self._issue(self._note("112", 1000))
        with self.assertRaisesRegex(ValidationError, "exceeds"):
            self._issue(self._note("112", 1500))

    def test_cancellation_must_credit_the_whole_untouched_export(self):
        with self.assertRaisesRegex(ValidationError, "whole export total"):
            self._issue(self._note("112", 1000, correction="1"))
        self._issue(self._note("112", 100))
        with self.assertRaisesRegex(ValidationError, "cancelled whole"):
            self._issue(self._note("112", 2369, correction="1"))

    def test_pending_debit_notes_add_no_capacity(self):
        with self.assertRaisesRegex(ValidationError, "only correct amounts"):
            self._issue(self._note("111", 100, correction="1"))
        debit = self._issue(self._note("111", 500))
        self.assertEqual(debit.export_root_document_id, self.root)
        with self.assertRaisesRegex(ValidationError, "exceeds"):
            self._issue(self._note("112", 2500))
        self._accept(debit)
        self.assertTrue(self._issue(self._note("112", 2500)))

    def test_root_link_is_immutable(self):
        note = self._issue(self._note("112", 10))
        with self.assertRaises(ValidationError):
            note.sudo().write({"export_root_document_id": False})
