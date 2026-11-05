# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import json
from copy import deepcopy
from unittest.mock import patch

from odoo import Command

from odoo.addons.l10n_cl_dte_account.services.dte_issuance_service import (
    DteIssuanceService,
)
from odoo.addons.l10n_cl_dte_boleta.services.boleta_snapshot import (
    BoletaSnapshotService,
)
from odoo.addons.l10n_cl_dte_note.services.note_snapshot import NoteSnapshotService
from odoo.addons.l10n_cl_dte_pos.services.pos_boleta_snapshot import (
    PosBoletaSnapshotService,
)
from odoo.addons.point_of_sale.tests.common import TestPoSCommon

from ..services.pos_line_mapping import PosLineMappingService


class PosReturnCase(TestPoSCommon):
    """Use native orders, payments and stock; replace only fiscal transport."""

    def setUp(self):
        super().setUp()
        self.env.user.groups_id += self.env.ref("l10n_cl_dte_core.group_dte_manager")
        self.company.write(
            {
                "country_id": self.env.ref("base.cl").id,
                "currency_id": self.env.ref("base.CLP").id,
                "dte_sii_environment": "demo",
                "dte_pos_returns_enabled": True,
                "l10n_cl_activity_description": "Synthetic test services",
                "dte_sii_activity_code": "620200",
            }
        )
        self.company.partner_id.write(
            {
                "name": "Synthetic POS Issuer",
                "vat": "76864280-K",
                "street": "Test 1",
                "city": "Santiago",
            }
        )
        self.customer.write(
            {"vat": "76864280-K", "country_id": self.env.ref("base.cl").id}
        )
        self.config = self.basic_config
        self.config.pricelist_id.currency_id = self.env.ref("base.CLP")
        self.tax_19 = self.taxes["tax7"]
        self.tax_19.write({"amount": 19, "name": "Test VAT 19%"})
        self.product = self.create_product(
            "Fiscal Return Product", self.categ_basic, 100, tax_ids=[self.tax_19.id]
        )
        self.issue_calls = []
        self.issue_patch = patch.object(
            DteIssuanceService, "issue", autospec=True, side_effect=self._issue
        )
        self.issue_patch.start()
        self.addCleanup(self.issue_patch.stop)
        self.caf_patch = patch.object(
            NoteSnapshotService, "_validate_caf", return_value=None
        )
        self.caf_patch.start()
        self.addCleanup(self.caf_patch.stop)

    def artifact(self, document, role, raw):
        return (
            self.env["l10n_cl.electronic.artifact"]
            .sudo()
            .create(
                {
                    "document_id": document.id,
                    "role": role,
                    "content": base64.b64encode(raw),
                    "mimetype": "application/json"
                    if role == "source_snapshot"
                    else "application/xml",
                }
            )
        )

    def _issue(self, service, source, adapter, code):
        self.issue_calls.append((source._name, source.id, code))
        service._check_issuance_access(source)
        source = source.sudo()
        if code == "61":
            adapter.validate_source(source, code)
            snapshot = adapter.capture_snapshot(
                source, None, 900 + len(self.issue_calls), code
            )
            values = adapter.prepare_document_values(source, code)
        else:
            order = source if source._name == "pos.order" else source.pos_order_ids
            lines, net, exempt = PosBoletaSnapshotService()._snapshot_lines(order)
            if source._name == "account.move":
                lines, net, exempt = BoletaSnapshotService()._snapshot_lines(source)
            snapshot = {
                "company_id": order.company_id.id,
                "document_code": code,
                "folio": 700 + len(self.issue_calls),
                "issue_date": "2026-11-05",
                "issuer": {
                    "vat": "76864280-K",
                    "name": "Synthetic POS Issuer",
                    "address": "Test 1",
                    "commune": "Santiago",
                    "city": "Santiago",
                    "activity": "Synthetic test services",
                    "activity_code": "620200",
                },
                "receiver": {
                    "vat": "76864280-K",
                    "name": "Synthetic Receiver",
                    "address": "Test 2",
                    "commune": "Santiago",
                    "city": "Santiago",
                },
                "totals": {
                    "net": str(net) if net else None,
                    "exempt": str(exempt) if exempt else None,
                    "vat": str(int(order.amount_tax)),
                    "total": str(int(order.amount_total)),
                },
                "lines": lines,
            }
            if order.session_id.dte_pos_returns_enabled:
                snapshot = PosLineMappingService().attach_snapshot_lineage(
                    snapshot,
                    order,
                    move=source if source._name == "account.move" else None,
                )
            values = {}
        document = (
            source.env["l10n_cl.electronic.document"]
            .sudo()
            .create(
                {
                    "name": "Synthetic POS evidence",
                    "company_id": source.company_id.id,
                    "sii_environment": "demo",
                    "document_code": code,
                    "source_key": adapter.source_key(source, code),
                    **adapter.document_source_values(source),
                    **values,
                }
            )
        )
        for state, role in (("authorized", "caf_source"), ("numbered", None)):
            document.action_record_transition(
                state,
                "synthetic_" + state,
                artifact_id=self.artifact(document, role, b"synthetic").id
                if role
                else False,
                folio_number=snapshot["folio"] if state == "numbered" else False,
            )
        evidence = self.artifact(
            document,
            "source_snapshot",
            json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode(),
        )
        document.action_record_source_snapshot(evidence.id)
        for state, role in (
            ("prepared", "unsigned_xml"),
            ("signed", "signed_xml"),
            ("queued", "sii_request"),
        ):
            document.action_record_transition(
                state,
                "synthetic_" + state,
                artifact_id=self.artifact(document, role, b"synthetic XML").id,
            )
        if code in {"39", "41"}:
            self.accept(document)
        return document

    def accept(self, document):
        for state, role in (
            ("submitted", "sii_response"),
            ("accepted", "status_response"),
        ):
            document.action_record_transition(
                state,
                "synthetic_" + state,
                artifact_id=self.artifact(document, role, b"synthetic response").id,
            )

    def sale(self, mode="session_summary", quantity=2, product=None):
        self.company.dte_pos_accounting_mode = mode
        self.open_new_session()
        data = self.create_ui_order_data(
            [(product or self.product, quantity)], customer=self.customer
        )
        response = self.env["pos.order"].sync_from_ui([data])
        return self.env["pos.order"].browse(response["pos.order"][0]["id"])

    def refund_data(self, original, quantity=1):
        data = self.create_ui_order_data(
            [(original.lines[0].product_id, -quantity)], customer=original.partner_id
        )
        data["dte_return_reason"] = "Synthetic goods return"
        data["lines"][0][2]["refunded_orderline_id"] = original.lines[0].id
        return data

    def refund(self, original, quantity=1, data=None):
        response = self.env["pos.order"].sync_from_ui(
            [deepcopy(data or self.refund_data(original, quantity))]
        )
        return self.env["pos.order"].browse(response["pos.order"][0]["id"])

    def close(self, session):
        cash = session.payment_method_ids.filtered("is_cash_count")[:1]
        amount = sum(
            session.order_ids.payment_ids.filtered(
                lambda payment: payment.payment_method_id == cash
            ).mapped("amount")
        )
        session.post_closing_cash_details(amount)
        session.close_session_from_ui()
        self.assertEqual(session.state, "closed")
        self.assertEqual(session.move_id.state, "posted")

    def other_store(self):
        cash = self.config.payment_method_ids.filtered("is_cash_count")[:1]
        journal = cash.journal_id.copy({"name": "Store B cash", "code": "RB01"})
        method = cash.copy({"name": "Store B cash", "journal_id": journal.id})
        location = self.env["stock.location"].create(
            {
                "name": "Store B stock",
                "usage": "internal",
                "company_id": self.company.id,
            }
        )
        picking_type = self.config.picking_type_id.copy(
            {"name": "Store B POS", "default_location_src_id": location.id}
        )
        self.config = self.config.copy(
            {
                "name": "Return Store B",
                "payment_method_ids": [Command.set(method.ids)],
                "picking_type_id": picking_type.id,
            }
        )
        self.open_new_session()
        return self.config

    def fiscal_response(self, document, state):
        document.action_record_transition(
            "submitted",
            "synthetic_submitted",
            artifact_id=self.artifact(
                document, "sii_response", b"synthetic response"
            ).id,
        )
        document.action_record_transition(
            state,
            "synthetic_status",
            artifact_id=self.artifact(
                document, "status_response", b"synthetic status"
            ).id,
        )
