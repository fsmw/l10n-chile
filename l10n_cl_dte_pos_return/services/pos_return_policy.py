# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from decimal import Decimal, InvalidOperation

from odoo import _
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import SQL

from .pos_fiscal_evidence import available_amounts, read_snapshot
from .pos_line_mapping import PosLineMappingService


class PosReturnPolicyService:
    """Validate advisory and committed returns against one fiscal root."""

    def decimal(self, value):
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError) as error:
            raise ValidationError(
                _("Enter a finite numeric return quantity or amount.")
            ) from error
        if not result.is_finite():
            raise ValidationError(
                _("Enter a finite numeric return quantity or amount.")
            )
        return result

    def check_actor(self, env, session):
        if not (
            env.user.has_group("point_of_sale.group_pos_user")
            and env.user.has_group("l10n_cl_dte_core.group_dte_user")
        ):
            raise AccessError(
                _("POS returns require both POS User and DTE User permissions.")
            )
        session.ensure_one()
        session.check_access("read")
        session.config_id.check_access("read")
        if (
            session.company_id not in env.companies
            or session.company_id.country_id.code != "CL"
        ):
            raise AccessError(_("Use an allowed Chilean POS company for this return."))
        if session.state != "opened" or not session.dte_pos_returns_enabled:
            raise ValidationError(
                _("Open a POS session with Chilean returns enabled first.")
            )
        if not session.company_id.sudo().dte_pos_returns_enabled:
            raise ValidationError(
                _("Chilean POS returns are disabled for this company.")
            )
        if (
            session.currency_id.name != "CLP"
            or session.company_id.sudo().dte_sii_environment != "demo"
        ):
            raise ValidationError(
                _("POS returns currently require CLP and Demo evidence.")
            )
        if session.config_id.cash_rounding:
            raise ValidationError(_("POS returns do not support cash rounding."))

    def original(self, original, session):
        self.check_actor(original.env, session)
        original.ensure_one()
        original.check_access("read")
        if (
            original.company_id != session.company_id
            or original.currency_id != session.currency_id
        ):
            raise AccessError(
                _("Return only sales of the current POS company and currency.")
            )
        documents = original._get_pos_dte_documents()
        if (
            original.state not in {"paid", "done", "invoiced"}
            or original.amount_total <= 0
            or len(documents) != 1
            or documents.document_code not in {"39", "41"}
            or documents.preparation_state != "queued"
            or documents.fiscal_state != "accepted"
            or documents.sii_environment != "demo"
        ):
            raise ValidationError(
                _("Select a paid POS sale with one accepted original boleta 39 or 41.")
            )
        mode = "per_order_invoice" if documents.source_move_id else "session_summary"
        if (
            mode == "per_order_invoice"
            and (
                documents.source_move_id != original.account_move
                or original.account_move.state != "posted"
            )
        ) or (
            mode == "session_summary"
            and (documents.source_pos_order_id != original or original.account_move)
        ):
            raise ValidationError(
                _(
                    "The original fiscal source and POS accounting mode are "
                    "inconsistent."
                )
            )
        snapshot = read_snapshot(documents)
        if (
            self.decimal(original.amount_total)
            != self.decimal(snapshot["totals"]["total"])
            or self.decimal(original.amount_tax)
            != self.decimal(snapshot["totals"].get("vat") or 0)
            or sum(
                (self.decimal(payment.amount) for payment in original.payment_ids),
                Decimal(0),
            )
            != self.decimal(original.amount_total)
        ):
            raise ValidationError(
                _(
                    "The original POS totals and payments do not reconcile "
                    "with its fiscal evidence."
                )
            )
        normalize = original.env["l10n_cl.caf.authorization"]._normalize_vat
        current_receiver = (
            original.partner_id.commercial_partner_id.vat
            if original.partner_id
            else "66666666-6"
        )
        if normalize(current_receiver) != normalize(snapshot["receiver"]["vat"]):
            raise ValidationError(
                _(
                    "The original POS customer no longer matches its frozen "
                    "fiscal receiver."
                )
            )
        return documents, mode

    def reason(self, value):
        reason = (value or "").strip()
        if not reason or len(reason) > 90:
            raise ValidationError(_("Enter a return reason of 1 to 90 characters."))
        try:
            reason.encode("ISO-8859-1")
        except UnicodeEncodeError as error:
            raise ValidationError(
                _("The return reason must use ISO-8859-1 characters.")
            ) from error
        return reason

    def _check_payment_method(self, method, session):
        if (
            method not in session.config_id.payment_method_ids
            or method.type == "pay_later"
            or method.use_payment_terminal
            or method.payment_method_type != "none"
            or ("is_online_payment" in method._fields and method.is_online_payment)
        ):
            raise ValidationError(
                _("Use a manual cash or bank refund method configured on this POS.")
            )

    def validate_payload(self, env, payload, session):
        self.check_actor(env, session)
        # Serialize with native session closing before locking the fiscal root.
        # Touching the row also makes a stale closing transaction retry under
        # PostgreSQL REPEATABLE READ rather than omit a newly committed return.
        env.cr.execute(
            SQL(
                "SELECT id FROM pos_session WHERE id = %s FOR UPDATE",
                session.id,
            )
        )
        session.invalidate_recordset(["state", "dte_pos_returns_enabled"])
        self.check_actor(env, session)
        session.write({"dte_pos_returns_enabled": session.dte_pos_returns_enabled})
        lines = payload.get("lines") or []
        if (
            not lines
            or len(lines) > 1000
            or any(
                command[0] not in {0, 1} or not isinstance(command[2], dict)
                for command in lines
            )
        ):
            raise ValidationError(
                _("A POS return requires explicit original product lines.")
            )
        claims = {
            "account_move",
            "dte_return_target_document_id",
            "dte_return_operation",
            "dte_return_accounting_mode",
        }
        if any(payload.get(name) for name in claims):
            raise AccessError(
                _("Fiscal POS return bindings are assigned only by the server.")
            )
        requested = []
        original_orders = env["pos.order"]
        for _command, _line_id, values in lines:
            if any(
                values.get(name)
                for name in (
                    "dte_return_origin_line_number",
                    "dte_return_map_artifact_id",
                )
            ):
                raise AccessError(
                    _("Fiscal POS line identities are assigned only by the server.")
                )
            qty = self.decimal(values.get("qty", 0))
            origin = (
                env["pos.order.line"]
                .browse(values.get("refunded_orderline_id"))
                .exists()
            )
            if (
                qty >= 0
                or len(origin) != 1
                or values.get("product_id") != origin.product_id.id
            ):
                raise ValidationError(
                    _("Use only negative product lines from one original POS sale.")
                )
            origin.check_access("read")
            if self.decimal(values.get("price_unit")) != self.decimal(
                origin.price_unit
            ) or self.decimal(values.get("discount", 0)) != self.decimal(
                origin.discount
            ):
                raise ValidationError(
                    _("A goods return must preserve the original price and discount.")
                )
            original_orders |= origin.order_id
            requested.append({"original_line_id": origin.id, "quantity": str(-qty)})
        if len(original_orders) != 1:
            raise ValidationError(
                _("A return can reference only one original POS sale.")
            )
        target, mode = self.original(original_orders, session)
        self._lock(target, original_orders)
        result = self._requested(
            original_orders, target, requested, exclude_uuid=payload.get("uuid")
        )
        line_amounts, totals = self.compute_amounts(original_orders, requested)
        for _command, _line_id, values in lines:
            origin = env["pos.order.line"].browse(values["refunded_orderline_id"])
            lot_commands = values.get("pack_lot_ids") or []
            if any(
                command[0] != 0 or not isinstance(command[2], dict)
                for command in lot_commands
            ):
                raise ValidationError(
                    _(
                        "Return lot identities must be new records linked to the "
                        "original sale."
                    )
                )
            self.validate_lots(
                origin,
                [command[2].get("lot_name") for command in lot_commands],
                -self.decimal(values["qty"]),
                payload.get("uuid"),
            )
            amounts = line_amounts[values["refunded_orderline_id"]]
            for key, expected in (
                ("price_subtotal", -amounts["base"]),
                ("price_subtotal_incl", -amounts["total"]),
            ):
                if session.currency_id.compare_amounts(
                    float(self.decimal(values.get(key))), expected
                ):
                    raise ValidationError(
                        _(
                            "The supplied return line amounts differ from native tax "
                            "computation."
                        )
                    )
                values[key] = expected
        for key, expected in (
            ("amount_total", -totals["total"]),
            ("amount_tax", -totals["vat"]),
        ):
            if session.currency_id.compare_amounts(
                float(self.decimal(payload.get(key))), expected
            ):
                raise ValidationError(
                    _("The supplied return totals differ from native tax computation.")
                )
            payload[key] = expected
        reason = self.reason(payload.get("dte_return_reason"))
        self._validate_payments(env, payload, session, totals["total"])
        return {
            "target": target,
            "original": original_orders,
            "mode": mode,
            "operation": result["operation"],
            "reason": reason,
        }

    def compute_amounts(self, original, requested):
        """Use Odoo's shared tax engine, including document-level rounding."""
        tax_model = original.env["account.tax"]
        lines = []
        ids = []
        for item in requested:
            source = original.lines.filtered(
                lambda line, item=item: line.id == item["original_line_id"]
            )
            base = tax_model._prepare_base_line_for_taxes_computation(
                {},
                product_id=source.product_id,
                tax_ids=source.tax_ids,
                price_unit=source.price_unit,
                discount=source.discount,
                quantity=float(self.decimal(item["quantity"])),
                currency_id=original.currency_id,
                rate=1.0,
                partner_id=original.partner_id.commercial_partner_id,
                is_refund=True,
            )
            lines.append(base)
            ids.append(source.id)
        tax_model._add_tax_details_in_base_lines(lines, original.company_id)
        tax_model._round_base_lines_tax_details(lines, original.company_id)
        result = {}
        totals = {"net": 0.0, "exempt": 0.0, "vat": 0.0, "total": 0.0}
        for source_id, base in zip(ids, lines, strict=True):
            details = base["tax_details"]
            excluded = details["total_excluded_currency"]
            included = details["total_included_currency"]
            result[source_id] = {"base": excluded, "total": included}
            component = (
                "net" if any(tax.amount for tax in base["tax_ids"]) else "exempt"
            )
            totals[component] += excluded
            totals["vat"] += included - excluded
            totals["total"] += included
        return result, totals

    def _lock(self, target, original):
        target._lock_note_chain(target, source=original)
        original.env.cr.execute(
            SQL(
                "SELECT id FROM pos_order WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
                original.ids,
            )
        )
        original.env.cr.execute(
            SQL(
                (
                    "SELECT id FROM pos_order_line WHERE order_id = ANY(%s) "
                    "ORDER BY id FOR UPDATE"
                ),
                original.ids,
            )
        )
        original.lines.invalidate_recordset()
        return target

    def lock_original(self, order):
        return self._lock(
            order.dte_return_target_document_id, order._get_dte_return_original_order()
        )

    def _requested(self, original, target, requested, exclude_uuid=None):
        if not isinstance(requested, list) or not requested or len(requested) > 1000:
            raise ValidationError(_("Select between 1 and 1000 original POS details."))
        mapping = PosLineMappingService().read_mapping(target)
        snapshot = read_snapshot(target)
        all_quantities = [
            {
                "original_line_id": entry["pos_line_id"],
                "quantity": snapshot["lines"][entry["ordinal"] - 1]["quantity"],
            }
            for entry in mapping["lines"]
        ]
        native_lines, native_totals = self.compute_amounts(original, all_quantities)
        if any(
            self.decimal(native_totals[key])
            != self.decimal(snapshot["totals"].get(key) or 0)
            for key in native_totals
        ):
            raise ValidationError(
                _(
                    "Current native tax settings no longer reproduce the "
                    "frozen original sale."
                )
            )
        if any(
            self.decimal(native_lines[entry["pos_line_id"]]["base"])
            != self.decimal(snapshot["lines"][entry["ordinal"] - 1]["subtotal"])
            for entry in mapping["lines"]
        ):
            raise ValidationError(
                _(
                    "Current native detail rounding differs from the frozen "
                    "original sale."
                )
            )
        ordinals = {
            entry["pos_line_id"]: entry["ordinal"] for entry in mapping["lines"]
        }
        available = available_amounts(target)
        values, seen = [], set()
        for item in requested:
            line = original.lines.filtered(
                lambda row, item=item: row.id == item.get("original_line_id")
            )
            quantity = self.decimal(item.get("quantity"))
            if (
                len(line) != 1
                or line.id in seen
                or quantity <= 0
                or line.id not in ordinals
            ):
                raise ValidationError(
                    _(
                        "Select each original fiscal product line once with a "
                        "positive quantity."
                    )
                )
            seen.add(line.id)
            if (
                line.combo_parent_id
                or line.combo_line_ids
                or ("is_reward_line" in line._fields and line.is_reward_line)
                or line.product_id == original.config_id.tip_product_id
            ):
                raise ValidationError(
                    _("Combo, reward and tip returns are outside this POS return flow.")
                )
            other = line.refund_orderline_ids.filtered(
                lambda row: (
                    row.order_id.state != "cancel" and row.order_id.uuid != exclude_uuid
                )
            )
            remaining = self.decimal(line.qty) + sum(
                (self.decimal(row.qty) for row in other), Decimal(0)
            )
            fiscal_remaining = self.decimal(
                available["lines"][ordinals[line.id]]["quantity"]
            )
            if quantity > min(remaining, fiscal_remaining):
                raise ValidationError(
                    _("The returned quantity exceeds the remaining original quantity.")
                )
            values.append(
                {
                    "original_line_id": line.id,
                    "ordinal": ordinals[line.id],
                    "remaining_qty": str(min(remaining, fiscal_remaining)),
                    "return_qty": str(quantity),
                }
            )
        snapshot = read_snapshot(target)
        untouched = all(
            self.decimal(snapshot["totals"].get(key) or 0) == available[key]
            for key in ("net", "exempt", "vat", "total")
        )
        full = len(values) == len(mapping["lines"]) and all(
            self.decimal(item["return_qty"])
            == self.decimal(original.lines.browse(item["original_line_id"]).qty)
            for item in values
        )
        operation = "cancel" if untouched and full else "return"
        if operation == "return" and len(values) > 60:
            raise ValidationError(
                _("A partial credit note supports at most 60 fiscal details.")
            )
        return {"lines": values, "operation": operation, "available": available}

    def classify_operation(self, order, original, available):
        requested = [
            {
                "original_line_id": line.refunded_orderline_id.id,
                "quantity": str(-self.decimal(line.qty)),
            }
            for line in order.lines
        ]
        return self._requested(
            original,
            order.dte_return_target_document_id,
            requested,
            exclude_uuid=order.uuid,
        )["operation"]

    def validate_return(self, order, *, lock=False):
        order.ensure_one()
        self.check_actor(order.env, order.session_id)
        original = order._get_dte_return_original_order()
        target, mode = self.original(original, order.session_id)
        if lock:
            self._lock(target, original)
        if (
            order.dte_return_target_document_id != target
            or order.dte_return_accounting_mode != mode
        ):
            raise ValidationError(
                _("The POS return fiscal provenance differs from its original sale.")
            )
        if order.partner_id != original.partner_id:
            raise ValidationError(
                _("The POS return must preserve the original sale customer.")
            )
        self.reason(order.dte_return_reason)
        if any(line.qty >= 0 or not line.refunded_orderline_id for line in order.lines):
            raise ValidationError(
                _("Mixed sales and returns are not supported in one fiscal order.")
            )
        requested = [
            {
                "original_line_id": line.refunded_orderline_id.id,
                "quantity": str(-self.decimal(line.qty)),
            }
            for line in order.lines
        ]
        result = self._requested(original, target, requested, exclude_uuid=order.uuid)
        if result["operation"] != order.dte_return_operation:
            raise ValidationError(
                _("Revalidate the return: the original adjustment capacity changed.")
            )
        for line in order.lines:
            origin = line.refunded_orderline_id
            if (
                line.product_id != origin.product_id
                or line.tax_ids != origin.tax_ids
                or line.product_uom_id != origin.product_uom_id
                or self.decimal(line.price_unit) != self.decimal(origin.price_unit)
                or self.decimal(line.discount) != self.decimal(origin.discount)
            ):
                raise ValidationError(
                    _(
                        "The return must preserve original product, tax, unit, "
                        "price and discount."
                    )
                )
        native_lines, native_totals = self.compute_amounts(original, requested)
        for line in order.lines:
            values = native_lines[line.refunded_orderline_id.id]
            if self.decimal(-line.price_subtotal) != self.decimal(
                values["base"]
            ) or self.decimal(-line.price_subtotal_incl) != self.decimal(
                values["total"]
            ):
                raise ValidationError(
                    _(
                        "The persisted POS return details differ from native tax "
                        "computation."
                    )
                )
        if self.decimal(-order.amount_total) != self.decimal(
            native_totals["total"]
        ) or self.decimal(-order.amount_tax) != self.decimal(native_totals["vat"]):
            raise ValidationError(
                _("The persisted POS return totals differ from native tax computation.")
            )
        for line in order.lines:
            self.validate_lots(
                line.refunded_orderline_id,
                line.pack_lot_ids.mapped("lot_name"),
                -self.decimal(line.qty),
                order.uuid,
            )
        for payment in order.payment_ids:
            self._check_payment_method(payment.payment_method_id, order.session_id)
            if payment.amount >= 0:
                raise ValidationError(_("A return payment must be negative."))
        return result

    def validate_native_result(self, order):
        self.validate_return(order)
        if (
            order.state not in {"paid", "invoiced"}
            or order.amount_total >= 0
            or not order.currency_id.is_zero(order.amount_paid - order.amount_total)
        ):
            raise ValidationError(
                _(
                    "The native POS refund is not completely paid; no credit "
                    "note was issued."
                )
            )
        if order.dte_return_accounting_mode == "per_order_invoice":
            self.authorize_accounting_source(order.account_move)
        elif order.account_move:
            raise ValidationError(
                _(
                    "A summarized POS return cannot create an individual "
                    "accounting credit."
                )
            )
        return True

    def authorize_accounting_source(self, move):
        move.ensure_one()
        order = move.dte_pos_return_order_id
        if not order:
            raise AccessError(
                _("The accounting credit has no verified native POS return source.")
            )
        self.check_actor(move.env, order.session_id)
        target = order.dte_return_target_document_id
        if (
            order.dte_return_accounting_mode != "per_order_invoice"
            or move != order.account_move
            or move.pos_order_ids != order
            or move.company_id != order.company_id
            or move.move_type != "out_refund"
            or move.state != "posted"
            or move.reversed_entry_id != target.source_move_id
            or move.dte_note_target_document_id != target
            or move.dte_note_operation != order.dte_return_operation
            or self.decimal(move.amount_total) != -self.decimal(order.amount_total)
        ):
            raise ValidationError(
                _("The accounting credit is not the exact posted native POS refund.")
            )
        return order

    def preview(self, env, config_id, original_order_id, requested_lines, reason):
        config = env["pos.config"].browse(config_id).exists()
        config.check_access("read")
        session = config.current_session_id
        self.check_actor(env, session)
        original = env["pos.order"].browse(original_order_id).exists()
        target, mode = self.original(original, session)
        self.reason(reason)
        result = self._requested(original, target, requested_lines)
        snapshot = read_snapshot(target)
        _line_amounts, native_totals = self.compute_amounts(original, requested_lines)
        totals = {key: int(self.decimal(value)) for key, value in native_totals.items()}
        return {
            "original_order_id": original.id,
            "original_document": {
                "id": target.id,
                "type": target.document_code,
                "folio": target.folio,
                "date": snapshot["issue_date"],
            },
            "accounting_mode": mode,
            "operation": result["operation"],
            "totals": totals,
            "lines": result["lines"],
            "revision": target.note_revision,
            "warnings": [],
        }

    def _validate_payments(self, env, payload, session, expected_total):
        paid = Decimal(0)
        for command in payload.get("payment_ids", []):
            if command[0] not in {0, 1}:
                raise ValidationError(_("Use explicit manual refund payments."))
            values = command[2]
            self._check_payment_method(
                env["pos.payment.method"].browse(values.get("payment_method_id")),
                session,
            )
            if self.decimal(values.get("amount", 0)) >= 0:
                raise ValidationError(_("A return payment must be negative."))
            amount = self.decimal(values["amount"])
            if amount != amount.to_integral_value():
                raise ValidationError(_("CLP refund payments must be whole pesos."))
            paid += amount
        if paid != -self.decimal(expected_total):
            raise ValidationError(
                _("Refund payments must equal the exact native credit total.")
            )

    def validate_lots(self, origin, names, quantity, exclude_uuid):
        tracking = origin.product_id.tracking
        original_names = set(origin.pack_lot_ids.mapped("lot_name"))
        if tracking == "none":
            if original_names or names:
                raise ValidationError(
                    _(
                        "The original product tracking can no longer be "
                        "represented natively."
                    )
                )
            return
        stock_names = set(
            origin.order_id.sudo()
            .picking_ids.move_line_ids.filtered(
                lambda line: line.product_id == origin.product_id
            )
            .lot_id.mapped("name")
        )
        if not original_names or not original_names <= stock_names:
            raise ValidationError(
                _(
                    "The original native stock movement has no matching lot "
                    "or serial trace."
                )
            )
        if (
            not names
            or len(set(names)) != len(names)
            or not set(names) <= original_names
        ):
            raise ValidationError(
                _("Return only lot or serial identities from the original POS line.")
            )
        if tracking == "lot" and len(original_names) != 1:
            raise ValidationError(
                _("This return flow requires one original lot per POS detail.")
            )
        if tracking == "serial":
            if quantity != len(names):
                raise ValidationError(
                    _("Provide exactly one original serial for each returned unit.")
                )
            previous = origin.order_id.lines.refund_orderline_ids.filtered(
                lambda line: (
                    line.product_id == origin.product_id
                    and line.order_id.state != "cancel"
                    and line.order_id.uuid != exclude_uuid
                )
            )
            if set(names).intersection(previous.pack_lot_ids.mapped("lot_name")):
                raise ValidationError(
                    _("An original serial has already been returned.")
                )
