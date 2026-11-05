# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from copy import deepcopy
from decimal import Decimal

from odoo import _, fields
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_note.services.note_adjustment import NoteAdjustmentService
from odoo.addons.l10n_cl_dte_note.services.note_reference_service import (
    NoteReferenceService,
)
from odoo.addons.l10n_cl_dte_note.services.note_snapshot import NoteSnapshotService
from odoo.addons.l10n_cl_dte_xml.services.invoice_snapshot import InvoiceSnapshotService

from .pos_return_policy import PosReturnPolicyService


class PosReturnSnapshotService:
    """Produce the existing note schema from a native negative POS order."""

    def validate_source(self, order, document_code):
        if str(document_code) != "61":
            raise ValidationError(_("POS returns support only credit note type 61."))
        policy = PosReturnPolicyService()
        result = policy.validate_return(order)
        policy.validate_native_result(order)
        effects, amounts = self.effects(order, result["available"])
        return {**result, "effects": effects, "amounts": amounts}

    def effects(self, order, available):
        math = NoteAdjustmentService()
        target = order.dte_return_target_document_id
        reference = NoteReferenceService(order.env)
        snapshot = reference.read_original_snapshot(target)
        effects = []
        for line in order.lines:
            ordinal = line.dte_return_origin_line_number
            if ordinal < 1 or ordinal > len(snapshot["lines"]):
                raise ValidationError(
                    _("A return detail has no verified original fiscal ordinal.")
                )
            source = snapshot["lines"][ordinal - 1]
            factor = Decimal(1) - Decimal(
                str(source.get("discount_percent") or 0)
            ) / Decimal(100)
            fiscal_unit = (
                Decimal(str(source["subtotal"]))
                / Decimal(str(source["quantity"]))
                / factor
                if factor
                else Decimal(0)
            )
            subtotal = math.integer(-line.price_subtotal, _("return detail"))
            if subtotal <= 0:
                raise ValidationError(
                    _("Return details must have positive fiscal credit amounts.")
                )
            effects.append(
                {
                    **deepcopy(source),
                    # NC61 serializes net fiscal prices. The native POS gross
                    # price and discount remain unchanged and are validated.
                    "unit_price": format(fiscal_unit.normalize(), "f"),
                    "origin_line_number": ordinal,
                    "quantity": format(Decimal(str(-line.qty)).normalize(), "f"),
                    "quantity_delta": format(Decimal(str(-line.qty)).normalize(), "f"),
                    "subtotal": str(subtotal),
                    "net": 0 if source["exempt"] else subtotal,
                    "exempt_amount": subtotal if source["exempt"] else 0,
                    "vat": 0,
                    "total": subtotal,
                }
            )
        vat = math.allocate_vat(
            math.integer(-order.amount_tax, _("return VAT")), effects
        )
        for effect in effects:
            effect["vat"] = vat[effect["origin_line_number"]]
            effect["total"] += effect["vat"]
        amounts = {
            "net": sum(e["net"] for e in effects),
            "exempt": sum(e["exempt_amount"] for e in effects),
            "vat": sum(e["vat"] for e in effects),
            "total": sum(e["total"] for e in effects),
        }
        if amounts["total"] != math.integer(-order.amount_total, _("return total")):
            raise ValidationError(
                _(
                    "The fiscal credit does not reconcile exactly with the "
                    "native POS refund."
                )
            )
        validator = NoteSnapshotService()
        validator._check_within(amounts, available)
        for effect in effects:
            capacity = available["lines"][effect["origin_line_number"]]
            if any(
                effect[k] > capacity[v]
                for k, v in (
                    ("net", "net"),
                    ("exempt_amount", "exempt"),
                    ("vat", "vat"),
                    ("total", "total"),
                )
            ):
                raise ValidationError(
                    _(
                        "A return detail exceeds the remaining fiscal amount; "
                        "CLP rounding cannot be adjusted silently."
                    )
                )
        if order.dte_return_operation == "cancel":
            validator._validate_full_credit_cancel(
                target, target, effects, amounts, available, reference
            )
        return effects, amounts

    def capture(self, order, caf, folio_number, document_code):
        result = self.validate_source(order, document_code)
        target = order.dte_return_target_document_id
        source = NoteReferenceService(order.env).read_original_snapshot(target)
        normalize = order.env["l10n_cl.caf.authorization"]._normalize_vat
        if normalize(order.company_id.partner_id.vat) != normalize(
            source["issuer"]["vat"]
        ):
            raise ValidationError(
                _("The company RUT no longer matches the original fiscal issuer.")
            )
        timestamp = InvoiceSnapshotService()._chile_timestamp()
        issue_date = (
            fields.Datetime.context_timestamp(
                order.with_context(tz="America/Santiago"), order.date_order
            )
            .date()
            .isoformat()
        )
        operation = order.dte_return_operation
        effects = result["effects"]
        totals = NoteAdjustmentService().format_totals(result["amounts"])
        return {
            "snapshot_schema": "l10n_cl_dte_note/1",
            "document_code": "61",
            "folio": int(folio_number),
            "issue_date": issue_date,
            "document_timestamp": timestamp,
            "ted_timestamp": timestamp,
            "company_id": order.company_id.id,
            "sii_environment": "demo",
            "issuer": source["issuer"],
            "receiver": source["receiver"],
            "totals": totals,
            "lines": NoteAdjustmentService().summary_lines(effects)
            if operation == "cancel"
            else effects,
            "reference": {
                "document_code": target.document_code,
                "folio": target.folio,
                "issue_date": source["issue_date"],
                "reference_code": "1" if operation == "cancel" else "3",
                "reason": order.dte_return_reason,
                "target_document_id": target.id,
                "root_document_id": target.id,
            },
            "provenance": {
                "target_document_id": target.id,
                "root_document_id": target.id,
                "target_move_id": target.source_move_id.id,
                "target_pos_order_id": order._get_dte_return_original_order().id,
                "return_pos_order_id": order.id,
                "target_snapshot_digest": target.payload_digest,
                "operation": operation,
            },
            "adjustment": {
                "effect": "credit",
                "amounts": totals,
                "line_effects": effects,
                "target_note_id": None,
                "serialization_mode": "full_cancel_summary"
                if operation == "cancel"
                else "detail",
            },
        }


class PosAccountingNoteSnapshotService(NoteSnapshotService):
    def _check_note_operator(self, move):
        PosReturnPolicyService().authorize_accounting_source(move)

    def _move_effects(self, move):
        order = PosReturnPolicyService().authorize_accounting_source(move)
        available = NoteReferenceService(move.env).calculate_available_amounts(
            order.dte_return_target_document_id
        )
        effects, amounts = PosReturnSnapshotService().effects(order, available)
        math = NoteAdjustmentService()
        if (
            math.integer(move.amount_untaxed, _("accounting net amount"))
            != amounts["net"] + amounts["exempt"]
            or math.integer(move.amount_tax, _("accounting VAT")) != amounts["vat"]
        ):
            raise ValidationError(
                _(
                    "The native accounting credit and POS fiscal components "
                    "do not reconcile."
                )
            )
        product_lines = move.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        if len(product_lines) != len(order.lines):
            raise ValidationError(
                _(
                    "The native credit needs one accounting detail per "
                    "returned POS line."
                )
            )
        for line in product_lines:
            source = line.dte_pos_order_line_id
            if (
                source.order_id != order
                or line.quantity != -source.qty
                or line.price_unit != source.price_unit
                or line.discount != source.discount
                or line.tax_ids != source.tax_ids
                or line.price_subtotal != -source.price_subtotal
                or line.dte_note_origin_line_number
                != source.dte_return_origin_line_number
            ):
                raise ValidationError(
                    _(
                        "The native accounting detail differs from its verified "
                        "POS return line."
                    )
                )
        return effects, amounts
