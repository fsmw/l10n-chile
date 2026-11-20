# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from lxml import etree

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from ..services.export_reference_service import ExportReferenceService
from ..services.export_xml_composer import ExportXmlComposer
from .common import ExportTestCase

NS = {"sii": "http://www.sii.cl/SiiDte"}
# Lifecycle steps after signing, with the evidence each one records.
REMAINING_STEPS = (
    ("queued", "sii_request"),
    ("submitted", "sii_response"),
    ("accepted", "status_response"),
)


@tagged("post_install", "-at_install")
class TestExportNotes(ExportTestCase):
    """Export notes 111/112 refer to an accepted 110 within its capacity."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("111", "112"):
            cls.document_types[code] = cls.env["l10n_latam.document.type"].search(
                [("country_id", "=", cls.chile.id), ("code", "=", code)], limit=1
            )

    def setUp(self):
        super().setUp()
        self.create_signing_profile()
        for code in ("110", "111", "112"):
            self.validate_authorization(
                document_code=code, folio_start=300, folio_end=320
            )
        self.root = self._accept(self._issue(self._export()))

    def _issue(self, move):
        return move.with_user(self.dte_manager).issue_dte()

    def _accept(self, document):
        document = document.with_user(self.dte_manager)
        for state, role in REMAINING_STEPS:
            if (state == "queued" and document.preparation_state == "queued") or (
                state == "submitted" and document.fiscal_state != "unknown"
            ):
                continue
            artifact = self.create_artifact(document, role, f"test {state}".encode())
            document.action_record_transition(
                state, f"test_{state}", artifact_id=artifact.id
            )
        self.assertEqual(document.fiscal_state, "accepted")
        return document

    def _note(self, code, amount, correction="3", **values):
        move_type = "out_refund" if code == "112" else "out_invoice"
        note = self.init_invoice(
            move_type,
            partner=self.buyer,
            invoice_date="2026-01-20",
            products=[self.product_a],
            taxes=[],
            currency=self.usd,
        )
        note.invoice_line_ids.write(
            {"quantity": 1, "price_unit": amount, "tax_ids": [(5, 0, 0)]}
        )
        note.write(
            {
                "l10n_latam_document_type_id": self.document_types[code].id,
                "dte_export_target_id": self.root.id,
                "dte_export_correction": correction,
                "dte_export_reason": "Price adjustment",
                "dte_export_rate": 951.10,
                "dte_export_rate_date": "2026-01-20",
                "dte_export_rate_source": "BCCh observed dollar 2026-01-20",
                **values,
            }
        )
        note.action_post()
        return note

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
