# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from decimal import Decimal
from unittest.mock import patch

from odoo import Command
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_boleta.services.boleta_snapshot import (
    BoletaSnapshotService,
)
from odoo.addons.l10n_cl_dte_note.services.note_reference_service import (
    NoteReferenceService,
)
from odoo.addons.l10n_cl_dte_signing.tests.common import SigningTestCase

from ..services.pos_return_issuance_adapter import PosReturnIssuanceAdapter
from .common import PosReturnCase


@tagged("post_install", "-at_install")
class TestMixedBoletaCreditNote(PosReturnCase, SigningTestCase):
    def setUp(self):
        super().setUp()
        self.dte_manager.sudo().write(
            {
                "company_id": self.company.id,
                "company_ids": [Command.link(self.company.id)],
            }
        )
        self.exempt_product = self.create_product(
            "Exempt Fiscal Return Product",
            self.categ_basic,
            50,
            tax_ids=[],
        )

    def _mixed_sale(self):
        self.company.dte_pos_accounting_mode = "session_summary"
        self.issue_patch.stop()
        try:
            self.open_new_session()
            data = self.create_ui_order_data(
                [(self.product, 1), (self.exempt_product, 1)],
                customer=self.customer,
            )
            with patch.object(
                BoletaSnapshotService,
                "_annual_135_uf_limit_for",
                return_value=Decimal("999999999"),
            ):
                response = self.env["pos.order"].sync_from_ui([data])
            order = self.env["pos.order"].browse(response["pos.order"][0]["id"])
        finally:
            self.issue_patch.start()
        document = order.dte_document_ids
        self.assertEqual(len(document), 1)
        self.accept(document)
        return order

    def _return_mixed_sale(self, original):
        data = self.create_ui_order_data(
            [(line.product_id, -1) for line in original.lines.sorted("id")],
            customer=original.partner_id,
        )
        data["dte_return_reason"] = "Synthetic mixed-sale return"
        for order_line, source_line in zip(
            data["lines"], original.lines.sorted("id"), strict=True
        ):
            order_line[2]["refunded_orderline_id"] = source_line.id
        return self._real_refund(original, data=data)

    def _real_refund(self, original, data=None):
        self.issue_patch.stop()
        try:
            return self.refund(original, data=data)
        finally:
            self.issue_patch.start()

    def _original_snapshot(self, original):
        return NoteReferenceService(self.env).read_original_snapshot(
            original.dte_document_ids
        )

    def _credit_snapshot(self, credit):
        return NoteReferenceService(self.env).read_original_snapshot(
            credit.dte_document_ids
        )

    def _assert_credit_is_signed(self, credit):
        document = credit.dte_document_ids
        self.assertEqual(len(document), 1)
        signed_artifacts = document.artifact_ids.filtered(
            lambda artifact: artifact.role == "signed_xml"
        )
        self.assertEqual(len(signed_artifacts), 1)
        self.assertEqual(document.preparation_state, "queued")
        signed_artifacts.action_verify_integrity()
        signed_xml = base64.b64decode(signed_artifacts.content, validate=True)
        PosReturnIssuanceAdapter().validate_document(signed_xml)

    def test_partial_nc61_preserves_taxable_amount_and_source_reference(self):
        self.validate_authorization(document_code="39", folio_start=830, folio_end=835)
        self.validate_authorization(document_code="61", folio_start=850, folio_end=855)
        self.create_signing_profile()
        original = self._mixed_sale()
        source_snapshot = self._original_snapshot(original)
        self.assertEqual(
            source_snapshot["totals"],
            {
                "net": "100",
                "exempt": "50",
                "vat": "19",
                "total": "169",
                "vat_rate": "19",
            },
        )

        credit = self._real_refund(original)
        credit_snapshot = self._credit_snapshot(credit)
        self._assert_credit_is_signed(credit)
        self.assertEqual(credit.dte_document_ids.document_code, "61")
        self.assertEqual(credit_snapshot["reference"]["document_code"], "39")
        self.assertEqual(
            credit_snapshot["reference"]["folio"],
            original.dte_document_ids.folio,
        )
        self.assertEqual(
            credit_snapshot["totals"],
            {
                "net": "100",
                "exempt": None,
                "vat": "19",
                "total": "119",
                "vat_rate": "19",
            },
        )
        self.assertEqual(
            NoteReferenceService(self.env).calculate_available_amounts(
                original.dte_document_ids
            )["total"],
            50,
        )

    def test_full_nc61_reconciles_taxable_exempt_and_vat_components(self):
        self.validate_authorization(document_code="39", folio_start=840, folio_end=845)
        self.validate_authorization(document_code="61", folio_start=860, folio_end=865)
        self.create_signing_profile()
        original = self._mixed_sale()
        source_snapshot = self._original_snapshot(original)
        self.assertEqual(
            source_snapshot["totals"],
            {
                "net": "100",
                "exempt": "50",
                "vat": "19",
                "total": "169",
                "vat_rate": "19",
            },
        )

        credit = self._return_mixed_sale(original)
        credit_snapshot = self._credit_snapshot(credit)
        self._assert_credit_is_signed(credit)
        self.assertEqual(credit.dte_document_ids.document_code, "61")
        self.assertEqual(credit_snapshot["reference"]["reference_code"], "1")
        self.assertEqual(credit_snapshot["reference"]["document_code"], "39")
        self.assertEqual(
            credit_snapshot["totals"],
            {
                "net": "100",
                "exempt": "50",
                "vat": "19",
                "total": "169",
                "vat_rate": "19",
            },
        )
        self.assertEqual(
            NoteReferenceService(self.env).calculate_available_amounts(
                original.dte_document_ids
            )["total"],
            0,
        )
