# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from odoo import fields
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_xml.services.invoice_snapshot import InvoiceSnapshotService

from .note_adjustment import NoteAdjustmentService
from .note_reference_service import NoteReferenceService


class NoteSnapshotService:
    """Freeze the adjustment, fiscal reference and source evidence before XML work."""

    SUPPORTED_DOCUMENT_CODES = frozenset({"61", "56"})

    def __init__(self, env=None):
        self.env = env

    def validate_note_source(self, move, document_code):
        """Validate role, source, arithmetic, note matrix and available amounts."""
        self.env = move.env
        move.ensure_one()
        document_code = str(document_code)
        if document_code not in self.SUPPORTED_DOCUMENT_CODES:
            raise ValidationError(
                move.env._("Only DTE note types 61 and 56 are supported.")
            )
        if (
            move.state != "posted"
            or move.l10n_latam_document_type_id.code != document_code
        ):
            raise ValidationError(
                move.env._(
                    "Publish the accounting note with its selected DTE type "
                    "before issuing."
                )
            )
        certification_case = bool(
            "dte_certification_run_line_id" in move._fields
            and move.dte_certification_run_line_id
        )
        if move.currency_id.name != "CLP" or (
            move.company_id.dte_sii_environment != "demo" and not certification_case
        ):
            raise ValidationError(
                move.env._("DTE notes currently require CLP and Demo mode.")
            )
        self._check_note_operator(move)
        reference_service = NoteReferenceService(move.env)
        target = move.dte_note_target_document_id
        root = reference_service.validate_reference(move, target)
        reason = (move.dte_note_reason or "").strip()
        if not reason or len(reason) > 90:
            raise ValidationError(
                move.env._("Enter a reference reason of 1 to 90 characters.")
            )
        try:
            reason.encode("ISO-8859-1")
        except UnicodeEncodeError as error:
            raise ValidationError(
                move.env._("The DTE reference reason must use ISO-8859-1 characters.")
            ) from error
        self._validate_caf(move, document_code)
        effects, amounts = self._move_effects(move)
        if amounts["total"] <= 0 and move.dte_note_operation != "text":
            raise ValidationError(
                move.env._("A DTE note must have a positive fiscal amount.")
            )
        if move.dte_note_operation == "text" and amounts["total"] != 0:
            raise ValidationError(
                move.env._("A text correction must not change fiscal amounts.")
            )
        if move.dte_note_operation != "cancel" and len(effects) > 60:
            raise ValidationError(
                move.env._(
                    "A partial DTE note supports at most 60 detail lines; "
                    "split this adjustment into multiple notes."
                )
            )
        self._validate_adjustment(
            move,
            document_code,
            target,
            root,
            effects,
            amounts,
            reference_service,
        )
        return {"root": root, "effects": effects, "amounts": amounts}

    def _validate_adjustment(
        self, move, document_code, target, root, effects, amounts, reference_service
    ):
        available = reference_service.calculate_available_amounts(root)
        if move.dte_note_operation == "text":
            return
        if document_code == "61":
            if move.dte_note_operation == "cancel":
                self._validate_full_credit_cancel(
                    target, root, effects, amounts, available, reference_service
                )
            else:
                self._validate_partial_credit(move, effects, amounts, available)
        elif move.dte_note_operation == "amount":
            if amounts["total"] <= 0:
                raise ValidationError(
                    move.env._("A debit note must increase the fiscal amount.")
                )
        elif move.dte_note_operation == "cancel":
            target_snapshot = reference_service.read_original_snapshot(target)
            if amounts != self._snapshot_totals(target_snapshot):
                raise ValidationError(
                    move.env._(
                        "Debit cancellation amounts must match the referenced "
                        "credit note."
                    )
                )
            self._assert_full_line_equivalence(effects, target_snapshot)

    def _validate_full_credit_cancel(
        self, target, root, effects, amounts, available, reference_service
    ):
        root_totals = self._snapshot_totals(
            reference_service.read_original_snapshot(root)
        )
        if any(available[key] != value for key, value in root_totals.items()):
            raise ValidationError(
                target.env._(
                    "A full cancellation is unavailable after another "
                    "effective adjustment; create a credit for the remaining "
                    "amount."
                )
            )
        target_snapshot = reference_service.read_original_snapshot(target)
        if amounts != self._snapshot_totals(target_snapshot):
            raise ValidationError(
                target.env._(
                    "Full cancellation amounts must match the complete referenced DTE."
                )
            )
        self._assert_full_line_equivalence(effects, target_snapshot)

    def _validate_partial_credit(self, move, effects, amounts, available):
        self._check_within(amounts, available)
        for effect in effects:
            line_available = available["lines"].get(effect["origin_line_number"])
            if not line_available or any(
                effect[key] > line_available[component]
                for key, component in (
                    ("net", "net"),
                    ("exempt_amount", "exempt"),
                    ("vat", "vat"),
                    ("total", "total"),
                )
            ):
                raise ValidationError(
                    move.env._(
                        "The note exceeds the available amount for an "
                        "original detail line."
                    )
                )
            if move.dte_note_operation == "return" and Decimal(
                str(effect["quantity_delta"])
            ) > Decimal(str(line_available["quantity"])):
                raise ValidationError(
                    move.env._(
                        "The returned quantity exceeds the remaining original quantity."
                    )
                )

    def capture_note(self, move, caf, folio_number, document_code):
        """Capture a schema-v1 snapshot from posted accounting amounts."""
        result = self.validate_note_source(move, document_code)
        target = move.dte_note_target_document_id
        reference_service = NoteReferenceService(move.env)
        source = reference_service.read_original_snapshot(target)
        reference = reference_service.build_reference(move, target)
        company = move.company_id
        issuer_snapshot = source["issuer"]
        current_issuer = InvoiceSnapshotService()._partner_snapshot(
            company.partner_id,
            activity=company.l10n_cl_activity_description,
            activity_code=company.dte_sii_activity_code,
        )
        normalize_vat = move.env["l10n_cl.caf.authorization"]._normalize_vat
        if normalize_vat(current_issuer["vat"]) != normalize_vat(
            issuer_snapshot["vat"]
        ):
            raise ValidationError(
                move.env._("The company RUT no longer matches the original DTE.")
            )
        lines = result["effects"]
        code = str(document_code)
        if code == "61" and move.dte_note_operation == "cancel":
            serialization_mode = "full_cancel_summary"
            lines = self._summary_lines(lines)
        else:
            serialization_mode = "detail"
            if len(lines) > 60:
                raise ValidationError(
                    move.env._(
                        "A partial DTE note supports at most 60 details; split "
                        "this adjustment into multiple notes."
                    )
                )
        now = datetime.now(ZoneInfo("America/Santiago")).strftime("%Y-%m-%dT%H:%M:%S")
        amounts = result["amounts"]
        adjustment_effect = (
            "restore_credit"
            if code == "56" and move.dte_note_operation == "cancel"
            else ("credit" if code == "61" else "debit")
        )
        return {
            "snapshot_schema": "l10n_cl_dte_note/1",
            "document_code": code,
            "folio": int(folio_number),
            "issue_date": fields.Date.to_date(move.invoice_date).isoformat(),
            "document_timestamp": now,
            "ted_timestamp": now,
            "company_id": company.id,
            "sii_environment": "demo",
            "issuer": issuer_snapshot,
            "receiver": source["receiver"],
            "totals": self._format_totals(amounts),
            "lines": lines,
            "reference": reference,
            "provenance": {
                "target_document_id": target.id,
                "target_move_id": target.source_move_id.id,
                "target_snapshot_digest": target.payload_digest,
                "root_document_id": result["root"].id,
                "operation": move.dte_note_operation,
            },
            "adjustment": {
                "effect": adjustment_effect,
                "amounts": self._format_totals(amounts),
                "line_effects": result["effects"],
                "target_note_id": target.id
                if adjustment_effect == "restore_credit"
                else None,
                "serialization_mode": serialization_mode,
            },
        }

    def _validate_caf(self, move, document_code):
        caf = move._select_dte_caf(move.company_id.dte_sii_environment, document_code)
        caf._check_caf_integrity()

    def _move_effects(self, move):
        self.env = move.env
        lines = move.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        if not lines:
            raise ValidationError(
                move.env._("A DTE note needs at least one product line.")
            )
        result = []
        net = exempt = Decimal(0)
        source_snapshot = NoteReferenceService(move.env).read_original_snapshot(
            move.dte_note_target_document_id
        )
        original_lines = (
            source_snapshot.get("adjustment", {}).get("line_effects")
            or source_snapshot["lines"]
        )
        lines_by_number = {
            int(item.get("origin_line_number", index)): item
            for index, item in enumerate(original_lines, start=1)
        }
        seen_ordinals = set()
        for line in lines:
            ordinal = line.dte_note_origin_line_number
            if ordinal < 1 or ordinal not in lines_by_number:
                raise ValidationError(
                    move.env._(
                        "A note line cannot be matched to its original fiscal detail."
                    )
                )
            if ordinal in seen_ordinals:
                raise ValidationError(
                    move.env._("Use one accounting detail for each original DTE line.")
                )
            seen_ordinals.add(ordinal)
            subtotal = self._integer(line.price_subtotal, move.env._("line amount"))
            if subtotal <= 0:
                raise ValidationError(
                    move.env._("Note detail amounts must be positive.")
                )
            original = lines_by_number[ordinal]
            original_quantity = Decimal(str(original.get("quantity") or "0"))
            quantity = Decimal(str(line.quantity))
            unit_price = Decimal(str(line.price_unit))
            original_unit_price = Decimal(str(original.get("unit_price") or "0"))
            discount = Decimal(str(line.discount or 0))
            original_discount = Decimal(str(original.get("discount_percent") or 0))
            if move.dte_note_operation == "return" and (
                quantity > original_quantity
                or unit_price != original_unit_price
                or discount != original_discount
            ):
                raise ValidationError(
                    move.env._(
                        "A goods return must use the original unit price and "
                        "discount and cannot exceed the original quantity."
                    )
                )
            if move.dte_note_operation == "amount" and quantity != original_quantity:
                raise ValidationError(
                    move.env._(
                        "An amount correction cannot change the original quantity."
                    )
                )
            is_exempt = bool(original.get("exempt"))
            if any(
                tax.amount and (tax.amount_type != "percent" or abs(tax.amount) != 19)
                for tax in line.tax_ids
            ):
                raise ValidationError(
                    move.env._("DTE notes support only 19% VAT and exempt lines.")
                )
            line_vat = self._integer(
                line.price_total - line.price_subtotal, move.env._("line VAT")
            )
            if is_exempt:
                exempt += subtotal
                if line_vat:
                    raise ValidationError(
                        move.env._("An exempt note line cannot include VAT.")
                    )
            else:
                net += subtotal
            result.append(
                {
                    "origin_line_number": ordinal,
                    "name": (original.get("name") or "")[:80],
                    "description": (line.name or original.get("description") or "")[
                        :1000
                    ],
                    "quantity": format(Decimal(str(line.quantity)).normalize(), "f"),
                    "unit_price": format(
                        Decimal(str(line.price_unit)).normalize(), "f"
                    ),
                    "discount_percent": format(
                        Decimal(str(line.discount)).normalize(), "f"
                    )
                    if line.discount
                    else None,
                    "subtotal": str(int(subtotal)),
                    "exempt": is_exempt,
                    "net": int(0 if is_exempt else subtotal),
                    "exempt_amount": int(subtotal if is_exempt else 0),
                    "vat": int(line_vat),
                    "total": int(subtotal + line_vat),
                    "quantity_delta": "0"
                    if move.dte_note_operation == "amount"
                    else format(Decimal(str(line.quantity)).normalize(), "f"),
                }
            )
        vat = self._allocate_vat(
            self._integer(move.amount_tax, move.env._("tax amount")), result
        )
        for item in result:
            if not item["exempt"]:
                item["vat"] = vat[item["origin_line_number"]]
                item["total"] = int(item["subtotal"]) + item["vat"]
        amount_values = {
            "net": int(net),
            "exempt": int(exempt),
            "vat": int(sum(vat.values())),
            "total": int(net + exempt + sum(vat.values())),
        }
        if amount_values["total"] != self._integer(
            move.amount_total, move.env._("total amount")
        ) or amount_values["vat"] != self._integer(
            move.amount_tax, move.env._("tax amount")
        ):
            raise ValidationError(
                move.env._(
                    "The note detail amounts do not reconcile with the posted "
                    "accounting total."
                )
            )
        return result, amount_values

    def _allocate_vat(self, vat_total, lines):
        return NoteAdjustmentService(self.env).allocate_vat(vat_total, lines)

    def _summary_lines(self, details):
        return NoteAdjustmentService(self.env).summary_lines(details)

    def _snapshot_totals(self, snapshot):
        return NoteAdjustmentService(self.env).snapshot_totals(snapshot)

    def _assert_full_line_equivalence(self, effects, snapshot):
        source_lines = (
            snapshot.get("adjustment", {}).get("line_effects") or snapshot["lines"]
        )
        source_by_number = {
            int(line.get("origin_line_number", index)): line
            for index, line in enumerate(source_lines, start=1)
        }
        if len(effects) != len(source_by_number):
            raise ValidationError(
                self.env._(
                    "A cancellation must reverse every referenced fiscal detail."
                )
            )
        for effect in effects:
            source = source_by_number.get(effect["origin_line_number"])
            if not source or (
                int(source.get("subtotal") or source.get("total") or 0)
                != int(effect["subtotal"])
                or Decimal(str(source.get("quantity") or 0))
                != Decimal(str(effect["quantity"]))
                or bool(source.get("exempt")) != bool(effect["exempt"])
            ):
                raise ValidationError(
                    self.env._(
                        "A cancellation must preserve every referenced detail "
                        "amount and quantity."
                    )
                )

    def _format_totals(self, values):
        return NoteAdjustmentService(self.env).format_totals(values)

    def _check_within(self, amounts, available):
        for key in ("net", "exempt", "vat", "total"):
            if amounts[key] > available[key]:
                raise ValidationError(
                    self.env._("The note exceeds the available %(component)s amount.")
                    % {"component": key}
                )

    def _integer(self, value, label):
        return NoteAdjustmentService(self.env).integer(value, label)

    def _check_note_operator(self, move):
        if not move.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            from odoo.exceptions import AccessError

            raise AccessError(
                move.env._("Only a DTE manager can issue credit or debit notes.")
            )
