# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import json
from decimal import Decimal

from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_pos.services.pos_boleta_snapshot import (
    PosBoletaSnapshotService,
)
from odoo.addons.l10n_cl_dte_xml.services.invoice_snapshot import InvoiceSnapshotService

from .binding_context import _NATIVE_POS_BINDING
from .pos_fiscal_evidence import read_snapshot


class PosLineMappingService:
    """Preserve source identities separately from immutable fiscal evidence."""

    SCHEMA = "l10n_cl_dte_pos_return.line_map/1"

    def _key(self, line):
        return (
            line.get("name", ""),
            line.get("description", ""),
            Decimal(str(line.get("quantity") or 0)),
            line.get("uom", ""),
            Decimal(str(line.get("unit_price") or 0)),
            Decimal(str(line.get("discount_percent") or 0)),
            Decimal(str(line.get("subtotal") or 0)),
            bool(line.get("exempt")),
        )

    def _native_lines(self, order, move=None):
        if move:
            lines = move.invoice_line_ids.filtered(
                lambda line: line.display_type == "product"
            )
            return [line.dte_pos_order_line_id for line in lines], lines
        lines = order.lines.filtered(lambda line: line.product_id and line.qty > 0)
        return list(lines), order.env["account.move.line"]

    def _source_unit_price(self, line, accounting=False):
        if not accounting:
            return Decimal(str(line.price_unit))
        factor = Decimal(1) - Decimal(str(line.discount)) / Decimal(100)
        return (
            Decimal(str(line.price_subtotal)) / Decimal(str(line.qty)) / factor
            if factor
            else Decimal(0)
        )

    def attach_snapshot_lineage(self, snapshot, order, move=None):
        native, accounting = self._native_lines(order, move)
        if len(native) != len(snapshot["lines"]) or any(not line for line in native):
            raise ValidationError(
                _("The fiscal details have no exact native POS line mapping.")
            )
        entries = []
        for ordinal, (line, fiscal) in enumerate(
            zip(native, snapshot["lines"], strict=True), 1
        ):
            if line.order_id != order or line.qty <= 0:
                raise ValidationError(
                    _("A fiscal detail belongs to a different POS sale.")
                )
            if (
                Decimal(str(line.qty)) != Decimal(str(fiscal["quantity"]))
                or self._source_unit_price(line, accounting=bool(move))
                != Decimal(str(fiscal["unit_price"]))
                or Decimal(str(line.discount))
                != Decimal(str(fiscal.get("discount_percent") or 0))
                or Decimal(str(line.price_subtotal)) != Decimal(str(fiscal["subtotal"]))
            ):
                raise ValidationError(
                    _("POS fiscal line amounts differ from the original sale.")
                )
            entries.append(
                {
                    "ordinal": ordinal,
                    "pos_line_id": line.id,
                    "pos_line_uuid": line.uuid,
                    "lot_names": sorted(line.pack_lot_ids.mapped("lot_name")),
                    "tracking": line.product_id.tracking,
                    "account_move_line_id": accounting[ordinal - 1].id
                    if accounting
                    else None,
                }
            )
        snapshot["pos_line_map"] = {"schema": self.SCHEMA, "lines": entries}
        return snapshot

    def persist_mapping(self, order, document):
        if document.pos_line_mapping_artifact_id:
            return self.read_mapping(document)
        snapshot = read_snapshot(document)
        lineage = snapshot.get("pos_line_map")
        if not lineage or lineage.get("schema") != self.SCHEMA:
            raise ValidationError(
                _("The original fiscal snapshot has no verified POS line identities.")
            )
        return self._persist(order, document, lineage["lines"])

    def _persist(self, order, document, lines):
        document._lock_note_chain(document, source=order)
        document.invalidate_recordset(["pos_line_mapping_artifact_id"])
        if document.pos_line_mapping_artifact_id:
            return self.read_mapping(document)
        value = {
            "schema": self.SCHEMA,
            "document_id": document.id,
            "company_id": order.company_id.id,
            "source_snapshot_digest": document.payload_digest,
            "pos_order_id": order.id,
            "pos_order_uuid": order.uuid,
            "lines": lines,
        }
        raw = json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
        artifact = (
            order.env["l10n_cl.electronic.artifact"]
            .sudo()
            .create(
                {
                    "document_id": document.id,
                    "role": "pos_line_mapping",
                    "content": base64.b64encode(raw),
                    "mimetype": "application/json",
                    "encoding": "UTF-8",
                    "producer_version": "l10n_cl_dte_pos_return/18.0",
                }
            )
        )
        document.with_context(
            _dte_pos_native_binding=_NATIVE_POS_BINDING
        )._set_pos_line_mapping_artifact(artifact)
        return self.read_mapping(document)

    def derive_legacy_mapping(self, order, document):
        if document.pos_line_mapping_artifact_id:
            return self.read_mapping(document)
        snapshot = read_snapshot(document)
        if snapshot.get("pos_line_map"):
            return self.persist_mapping(order, document)
        native, _taxable_net, _exempt_net = PosBoletaSnapshotService()._snapshot_lines(
            order
        )
        order_lines = order.lines.filtered(
            lambda line: line.product_id and line.qty > 0
        )
        if order.account_move:
            for candidate, line in zip(native, order_lines, strict=True):
                candidate["unit_price"] = str(
                    self._source_unit_price(line, accounting=True)
                )
        if len(native) != len(snapshot["lines"]):
            raise ValidationError(
                _("Legacy fiscal details cannot be matched uniquely to the POS sale.")
            )
        entries, used = [], set()
        for ordinal, fiscal in enumerate(snapshot["lines"], 1):
            candidates = [
                index
                for index, values in enumerate(native)
                if self._key(values) == self._key(fiscal)
            ]
            if len(candidates) != 1 or candidates[0] in used:
                raise ValidationError(
                    _(
                        "Legacy POS line mapping is ambiguous; no fiscal "
                        "identity was guessed."
                    )
                )
            index = candidates[0]
            used.add(index)
            line = order_lines[index]
            invoice_lines = order.account_move.invoice_line_ids.filtered(
                lambda item, line=line: (
                    item.display_type == "product"
                    and item.product_id == line.product_id
                    and Decimal(str(item.quantity)) == Decimal(str(line.qty))
                    and Decimal(str(item.price_subtotal))
                    == Decimal(str(line.price_subtotal))
                )
            )
            if order.account_move and len(invoice_lines) != 1:
                raise ValidationError(
                    _("The original accounting line cannot be identified uniquely.")
                )
            entries.append(
                {
                    "ordinal": ordinal,
                    "pos_line_id": line.id,
                    "pos_line_uuid": line.uuid,
                    "lot_names": sorted(line.pack_lot_ids.mapped("lot_name")),
                    "tracking": line.product_id.tracking,
                    "account_move_line_id": invoice_lines.id if invoice_lines else None,
                }
            )
        return self._persist(order, document, entries)

    def read_mapping(self, document):
        document.ensure_one()
        artifact = document.pos_line_mapping_artifact_id
        if (
            not artifact
            or artifact.document_id != document
            or artifact.role != "pos_line_mapping"
        ):
            raise ValidationError(
                _(
                    "Prepare an unambiguous original POS fiscal line mapping "
                    "before refunding."
                )
            )
        artifact.sudo().action_verify_integrity()
        try:
            data = json.loads(base64.b64decode(artifact.sudo().content, validate=True))
        except (ValueError, TypeError) as error:
            raise ValidationError(
                _("The POS fiscal mapping evidence is invalid.")
            ) from error
        snapshot = read_snapshot(document)
        order = document.env["pos.order"].browse(data.get("pos_order_id")).exists()
        if (
            data.get("schema") != self.SCHEMA
            or data.get("document_id") != document.id
            or data.get("company_id") != document.company_id.id
            or data.get("source_snapshot_digest") != document.payload_digest
            or len(order) != 1
            or order.company_id != document.company_id
            or order.uuid != data.get("pos_order_uuid")
            or len(data.get("lines", [])) != len(snapshot["lines"])
        ):
            raise ValidationError(
                _("The POS fiscal mapping does not match its immutable source.")
            )
        seen = set()
        for index, entry in enumerate(data["lines"], 1):
            line = order.lines.filtered(
                lambda item, entry=entry: item.id == entry.get("pos_line_id")
            )
            if (
                len(line) != 1
                or line.id in seen
                or entry.get("ordinal") != index
                or entry.get("pos_line_uuid") != line.uuid
            ):
                raise ValidationError(
                    _("A POS fiscal detail identity is inconsistent.")
                )
            seen.add(line.id)
            if document.source_move_id:
                accounting = document.source_move_id.invoice_line_ids.filtered(
                    lambda row, entry=entry: row.id == entry.get("account_move_line_id")
                )
                if (
                    len(accounting) != 1
                    or accounting.product_id != line.product_id
                    or Decimal(str(accounting.price_unit))
                    != Decimal(str(line.price_unit))
                    or Decimal(str(accounting.discount)) != Decimal(str(line.discount))
                ):
                    raise ValidationError(
                        _(
                            "The original invoice and POS line economics no longer "
                            "match."
                        )
                    )
            if entry.get(
                "tracking", line.product_id.tracking
            ) != line.product_id.tracking or entry.get(
                "lot_names", sorted(line.pack_lot_ids.mapped("lot_name"))
            ) != sorted(line.pack_lot_ids.mapped("lot_name")):
                raise ValidationError(
                    _(
                        "The original POS tracking identity differs from its "
                        "frozen line mapping."
                    )
                )
            fiscal = snapshot["lines"][index - 1]
            if (line.product_uom_id.name or "")[:4] != fiscal.get("uom", "") or bool(
                fiscal.get("exempt")
            ) == InvoiceSnapshotService()._is_taxable_19_percent(line.tax_ids):
                raise ValidationError(
                    _(
                        "The original fiscal tax or unit can no longer be "
                        "represented by the native POS line."
                    )
                )
            if (
                Decimal(str(line.qty)) != Decimal(str(fiscal["quantity"]))
                or self._source_unit_price(
                    line, accounting=bool(document.source_move_id)
                )
                != Decimal(str(fiscal["unit_price"]))
                or Decimal(str(line.discount))
                != Decimal(str(fiscal.get("discount_percent") or 0))
                or Decimal(str(line.price_subtotal)) != Decimal(str(fiscal["subtotal"]))
            ):
                raise ValidationError(
                    _(
                        "The original POS economics no longer match the frozen "
                        "fiscal detail."
                    )
                )
        return data
