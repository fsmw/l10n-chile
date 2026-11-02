# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
import json
from decimal import Decimal

from odoo import _, fields
from odoo.exceptions import ValidationError


class NoteReferenceService:
    """Read fiscal evidence and compute the available adjustment capacity."""

    ROOT_DOCUMENT_CODES = frozenset({"33", "34", "39", "41"})

    def __init__(self, env=None):
        self.env = env

    def read_original_snapshot(self, document):
        """Return the unique immutable snapshot after checking its evidence chain."""
        document.ensure_one()
        events = document.event_ids.filtered(
            lambda event: event.event_code == "source_snapshot_recorded"
        )
        if len(events) != 1 or len(events.artifact_id) != 1:
            raise ValidationError(
                _("The referenced DTE has no unique source snapshot.")
            )
        artifact = events.artifact_id
        if artifact.document_id != document or artifact.role != "source_snapshot":
            raise ValidationError(_("The referenced DTE snapshot evidence is invalid."))
        artifact.action_verify_integrity()
        raw = base64.b64decode(artifact.content, validate=True)
        digest = hashlib.sha256(raw).hexdigest()
        if digest != artifact.sha256 or digest != document.payload_digest:
            raise ValidationError(
                _("The referenced DTE snapshot digest does not match.")
            )
        try:
            snapshot = json.loads(raw.decode("UTF-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValidationError(
                _("The referenced DTE snapshot is unreadable.")
            ) from error
        required = {
            "document_code",
            "folio",
            "issue_date",
            "issuer",
            "receiver",
            "totals",
            "lines",
        }
        if not isinstance(snapshot, dict) or not required.issubset(snapshot):
            raise ValidationError(
                _("The referenced DTE snapshot has an unknown schema.")
            )
        if (
            str(snapshot["document_code"]) != document.document_code
            or int(snapshot["folio"]) != document.folio
        ):
            raise ValidationError(
                _("The fiscal snapshot identity differs from its DTE.")
            )
        return snapshot

    def validate_reference(self, move, target_document):
        """Enforce supported type pairs, accepted Demo state and accounting links."""
        move.ensure_one()
        target_document.ensure_one()
        code = str(move.l10n_latam_document_type_id.code or "")
        operation = move.dte_note_operation
        target_code = str(target_document.document_code)
        self.validate_single_reference(move)
        if (
            target_document.fiscal_state != "accepted"
            or target_document.preparation_state != "queued"
        ):
            raise ValidationError(_("Only an accepted DTE can be referenced."))
        if (
            target_document.sii_environment != "demo"
            or move.company_id.dte_sii_environment != "demo"
        ):
            raise ValidationError(
                _("DTE credit and debit notes are available in Demo mode only.")
            )
        if target_document.company_id != move.company_id:
            raise ValidationError(
                _("The note and referenced DTE must belong to the same company.")
            )
        source_snapshot = self.read_original_snapshot(target_document)
        if (
            code == "61"
            and target_code in self.ROOT_DOCUMENT_CODES
            and operation in {"cancel", "return", "amount"}
        ):
            if (
                move.move_type != "out_refund"
                or move.reversed_entry_id != target_document.source_move_id
            ):
                raise ValidationError(
                    _("A credit note must reverse the selected customer document.")
                )
            root = target_document.note_root_document_id or target_document
        elif code == "56" and target_code in {"33", "34"} and operation == "amount":
            if (
                move.move_type != "out_invoice"
                or move.debit_origin_id != target_document.source_move_id
            ):
                raise ValidationError(
                    _("A debit note must be linked to the selected customer invoice.")
                )
            root = target_document.note_root_document_id or target_document
        elif code == "56" and target_code == "61" and operation == "cancel":
            if (
                move.move_type != "out_invoice"
                or move.debit_origin_id != target_document.source_move_id
            ):
                raise ValidationError(
                    _(
                        "A debit cancellation must be linked to the selected "
                        "credit note."
                    )
                )
            root = target_document.note_root_document_id
            if not root or root.document_code not in self.ROOT_DOCUMENT_CODES:
                raise ValidationError(
                    _("The referenced credit note has no supported fiscal root.")
                )
        else:
            raise ValidationError(
                _("DTE type %(code)s cannot %(operation)s referenced type %(target)s.")
                % {
                    "code": code,
                    "operation": operation or "adjust",
                    "target": target_code,
                }
            )
        target_move = target_document.source_move_id
        if (
            not target_move
            or move.partner_id.commercial_partner_id
            != target_move.partner_id.commercial_partner_id
        ):
            raise ValidationError(
                _(
                    "The note recipient must match the fiscal recipient of the "
                    "referenced DTE."
                )
            )
        if move.invoice_date and fields.Date.to_date(
            move.invoice_date
        ) < fields.Date.to_date(source_snapshot["issue_date"]):
            raise ValidationError(_("The note date cannot precede its referenced DTE."))
        if target_document.note_target_document_id and code == "56":
            existing = self.env["l10n_cl.electronic.document"].search_count(
                [
                    ("note_target_document_id", "=", target_document.id),
                    ("source_move_id", "!=", move.id),
                    ("document_code", "=", "56"),
                    ("fiscal_state", "!=", "rejected"),
                ]
            )
            if existing:
                raise ValidationError(
                    _(
                        "This credit note already has a debit cancellation in "
                        "progress or accepted."
                    )
                )
        return root

    def validate_single_reference(self, move):
        """Reject legacy imported reference rows beside the DTE note target."""
        if "l10n_cl_reference_ids" in move._fields and move.l10n_cl_reference_ids:
            raise ValidationError(
                _(
                    "DTE notes support one fiscal reference; remove the other "
                    "imported invoice references."
                )
            )
        return True

    def calculate_available_amounts(self, root_document):
        """Subtract reserved credits and restore accepted debit cancellations."""
        root_document.ensure_one()
        original = self.read_original_snapshot(root_document)
        totals = self._snapshot_amounts(original)
        lines = self._initial_line_amounts(original)
        notes = self.env["l10n_cl.electronic.document"].search(
            [
                ("note_root_document_id", "=", root_document.id),
            ],
            order="id",
        )
        by_id = {}
        for note in notes:
            if note.preparation_state != "queued":
                continue
            snap = self.read_original_snapshot(note)
            by_id[note.id] = snap
            if note.document_code == "61" and note.fiscal_state != "rejected":
                self._apply_amounts(totals, self._snapshot_amounts(snap), -1)
                self._apply_line_effects(lines, snap, -1)
        for note in notes.filtered(
            lambda item: item.document_code == "56" and item.fiscal_state == "accepted"
        ):
            target = note.note_target_document_id
            if target and target.document_code == "61" and target.id in by_id:
                target_snapshot = by_id[target.id]
                self._apply_amounts(totals, self._snapshot_amounts(target_snapshot), 1)
                self._apply_line_effects(lines, target_snapshot, 1)
        if any(value < 0 for value in totals.values()) or any(
            value < 0 for values in lines.values() for value in values.values()
        ):
            raise ValidationError(
                _("Existing DTE adjustments exceed the original fiscal amount.")
            )
        return {
            **{key: int(value) for key, value in totals.items()},
            "lines": {
                ordinal: {
                    **{
                        key: int(value)
                        for key, value in values.items()
                        if key != "quantity"
                    },
                    "quantity": format(values["quantity"].normalize(), "f"),
                }
                for ordinal, values in lines.items()
            },
        }

    def build_reference(self, move, target_document):
        """Build the single SII reference and its immutable provenance identifiers."""
        snapshot = self.read_original_snapshot(target_document)
        reason = (move.dte_note_reason or "").strip()
        if not reason or len(reason) > 90:
            raise ValidationError(
                _("Enter a DTE reference reason of 1 to 90 characters.")
            )
        code = "1" if move.dte_note_operation == "cancel" else "3"
        return {
            "document_code": target_document.document_code,
            "folio": target_document.folio,
            "issue_date": snapshot["issue_date"],
            "reference_code": code,
            "reason": reason,
            "target_document_id": target_document.id,
            "root_document_id": target_document.note_root_document_id.id
            or target_document.id,
        }

    def _snapshot_amounts(self, snapshot):
        totals = snapshot["totals"]
        values = {
            key: Decimal(str(totals.get(key) or 0))
            for key in ("net", "exempt", "vat", "total")
        }
        if sum(values[key] for key in ("net", "exempt", "vat")) != values["total"]:
            # Invoice snapshots use net + exempt + VAT; boleta snapshots may omit
            # net VAT rate.
            if values["net"] + values["exempt"] + values["vat"] != values["total"]:
                raise ValidationError(_("The fiscal snapshot totals do not reconcile."))
        return values

    def _apply_amounts(self, target, values, sign):
        for key in target:
            target[key] += values[key] * sign

    def _initial_line_amounts(self, snapshot):
        """Allocate document VAT with deterministic largest remainders."""
        result = {}
        taxable = []
        for ordinal, line in enumerate(snapshot["lines"], start=1):
            subtotal = Decimal(str(line["subtotal"]))
            exempt = bool(line.get("exempt"))
            result[ordinal] = {
                "net": Decimal(0) if exempt else subtotal,
                "exempt": subtotal if exempt else Decimal(0),
                "vat": Decimal(0),
                "total": subtotal,
                "quantity": Decimal(str(line.get("quantity") or 0)),
            }
            if not exempt and subtotal:
                taxable.append((ordinal, subtotal))
        vat_total = Decimal(str(snapshot["totals"].get("vat") or 0))
        taxable_net = sum((amount for _, amount in taxable), Decimal(0))
        floors = {}
        remainders = []
        assigned = Decimal(0)
        if taxable_net:
            for ordinal, amount in taxable:
                exact = vat_total * amount / taxable_net
                floor = exact.to_integral_value(rounding="ROUND_FLOOR")
                floors[ordinal] = floor
                assigned += floor
                remainders.append((exact - floor, ordinal))
            remaining = int(vat_total - assigned)
            for _, ordinal in sorted(remainders, key=lambda item: (-item[0], item[1]))[
                :remaining
            ]:
                floors[ordinal] += 1
        for ordinal, amount in floors.items():
            result[ordinal]["vat"] = amount
            result[ordinal]["total"] += amount
        return result

    def _apply_line_effects(self, available, snapshot, sign):
        for effect in snapshot.get("adjustment", {}).get("line_effects", []):
            ordinal = int(effect["origin_line_number"])
            if ordinal not in available:
                raise ValidationError(
                    _("A note effect references a missing original fiscal detail.")
                )
            target = available[ordinal]
            target["net"] += Decimal(str(effect.get("net") or 0)) * sign
            target["exempt"] += Decimal(str(effect.get("exempt_amount") or 0)) * sign
            target["vat"] += Decimal(str(effect.get("vat") or 0)) * sign
            target["total"] += Decimal(str(effect.get("total") or 0)) * sign
            target["quantity"] += Decimal(str(effect.get("quantity_delta") or 0)) * sign
