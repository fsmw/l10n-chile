# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.l10n_cl_dte_issuance.services.dte_issuance_service import (
    DteIssuanceService,
)

from ..services.binding_context import _NATIVE_POS_BINDING, is_native_binding
from ..services.pos_fiscal_evidence import read_snapshot
from ..services.pos_line_mapping import PosLineMappingService
from ..services.pos_return_issuance_adapter import PosReturnIssuanceAdapter
from ..services.pos_return_policy import PosReturnPolicyService
from ..services.pos_sale_issuance_adapter import PosTrackedBoletaIssuanceAdapter


class PosOrder(models.Model):
    _inherit = "pos.order"

    dte_document_code = fields.Selection(
        selection_add=[("61", "Electronic credit note")],
    )
    dte_return_target_document_id = fields.Many2one(
        "l10n_cl.electronic.document",
        string="Original Boleta",
        readonly=True,
        index=True,
        copy=False,
        check_company=True,
        ondelete="restrict",
    )
    dte_return_operation = fields.Selection(
        [("cancel", "Full cancellation"), ("return", "Goods return")],
        string="Credit Operation",
        readonly=True,
        copy=False,
    )
    dte_return_reason = fields.Char(string="Return Reason", size=90, copy=False)
    dte_return_accounting_mode = fields.Selection(
        [
            ("per_order_invoice", "Invoice per order"),
            ("session_summary", "Session summary"),
        ],
        string="Original Accounting Mode",
        readonly=True,
        copy=False,
    )
    dte_pos_mapping_state = fields.Selection(
        [("unmapped", "Not mapped"), ("mapped", "Mapped"), ("ambiguous", "Ambiguous")],
        string="Fiscal Line Mapping",
        compute="_compute_dte_pos_mapping_state",
    )

    @api.depends(
        "dte_document_ids.pos_line_mapping_artifact_id",
        "account_move.dte_document_ids.pos_line_mapping_artifact_id",
    )
    def _compute_dte_pos_mapping_state(self):
        for order in self:
            docs = order._get_pos_dte_documents()
            order.dte_pos_mapping_state = (
                "mapped"
                if len(docs) == 1 and docs.pos_line_mapping_artifact_id
                else "unmapped"
            )

    @api.depends(
        "lines.tax_ids",
        "state",
        "account_move.l10n_latam_document_type_id",
        "dte_return_target_document_id",
    )
    def _compute_dte_document_code(self):
        result = super()._compute_dte_document_code()
        for order in self.filtered("dte_return_target_document_id"):
            order.dte_document_code = "61"
        return result

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not is_native_binding(self.env) and any(
                vals.get(key)
                for key in (
                    "account_move",
                    "dte_return_target_document_id",
                    "dte_return_operation",
                    "dte_return_accounting_mode",
                )
            ):
                raise AccessError(
                    _(
                        "Fiscal POS bindings are assigned only through validated "
                        "native processing."
                    )
                )
        return super().create(vals_list)

    def write(self, vals):
        bindings = {
            "account_move",
            "dte_return_target_document_id",
            "dte_return_operation",
            "dte_return_accounting_mode",
        }
        guarded = bindings - {"account_move"}
        if any(order.company_id.country_id.code == "CL" for order in self):
            guarded.add("account_move")
        if guarded.intersection(vals) and not is_native_binding(self.env):
            raise AccessError(_("Fiscal POS bindings cannot be changed by direct RPC."))
        protected = bindings | {
            "lines",
            "payment_ids",
            "amount_total",
            "amount_tax",
            "amount_paid",
            "amount_return",
            "partner_id",
            "company_id",
            "session_id",
            "currency_id",
            "dte_return_reason",
            "uuid",
            "to_invoice",
            "date_order",
        }
        if protected.intersection(vals) and any(
            order._get_pos_dte_documents() for order in self
        ):
            raise UserError(
                _(
                    "The fiscal POS order cannot change after DTE evidence "
                    "has been created."
                )
            )
        if vals.get("state") in {"draft", "cancel"} and any(
            order._get_pos_dte_documents() for order in self
        ):
            raise UserError(
                _("An emitted fiscal POS order cannot be reopened or cancelled.")
            )
        return super().write(vals)

    def unlink(self):
        if any(order._has_fiscal_pos_evidence() for order in self):
            raise UserError(_("A POS order with fiscal evidence cannot be deleted."))
        return super().unlink()

    def _get_dte_return_original_order(self):
        self.ensure_one()
        originals = self.lines.refunded_orderline_id.order_id
        if len(originals) != 1:
            raise ValidationError(_("A return requires exactly one original POS sale."))
        return originals

    @api.model
    def _process_order(self, order, existing_order):
        payload = dict(order)
        session = self.env["pos.session"].browse(payload.get("session_id")).exists()
        negative = any(
            command[0] in {0, 1}
            and isinstance(command[2], dict)
            and command[2].get("qty", 0) < 0
            for command in payload.get("lines", [])
        )
        if (
            not negative
            or not session
            or session.company_id.country_id.code != "CL"
            or not session.dte_pos_returns_enabled
        ):
            return super()._process_order(order, existing_order)
        if payload.get("state") == "draft":
            raise UserError(
                _(
                    "Complete Chilean POS returns online; server-side refund "
                    "drafts are not supported."
                )
            )
        policy = PosReturnPolicyService(self.env)
        result = policy.validate_payload(self.env, payload, session)
        if existing_order and (
            existing_order.session_id != session
            or existing_order.user_id != self.env.user
        ):
            raise AccessError(
                _("A refund draft belongs to another POS session or operator.")
            )
        original = result["original"]
        payload.update(
            {
                "dte_return_target_document_id": result["target"].id,
                "dte_return_operation": result["operation"],
                "dte_return_accounting_mode": result["mode"],
                "dte_return_reason": result["reason"],
                "partner_id": original.partner_id.id,
                "user_id": self.env.uid,
                "fiscal_position_id": original.fiscal_position_id.id,
            }
        )
        mapping = PosLineMappingService(self.env).read_mapping(result["target"])
        ordinals = {
            entry["pos_line_id"]: entry["ordinal"] for entry in mapping["lines"]
        }
        payload["lines"] = [
            (
                command[0],
                command[1],
                {
                    **command[2],
                    "dte_return_origin_line_number": ordinals[
                        command[2]["refunded_orderline_id"]
                    ],
                    "dte_return_map_artifact_id": result[
                        "target"
                    ].pos_line_mapping_artifact_id.id,
                },
            )
            for command in payload["lines"]
        ]
        # The savepoint also protects callers which catch a sync error inside a request.
        with self.env.cr.savepoint():
            return super(
                PosOrder, self.with_context(_dte_pos_native_binding=_NATIVE_POS_BINDING)
            )._process_order(payload, existing_order)

    @api.model
    def _get_pos_dte_to_invoice(self, payload, session):
        if is_native_binding(self.env) and payload.get("dte_return_target_document_id"):
            return payload["dte_return_accounting_mode"] == "per_order_invoice"
        return super()._get_pos_dte_to_invoice(payload, session)

    def _check_pos_dte_order_before_processing(self, draft):
        if self.dte_return_target_document_id:
            PosReturnPolicyService(self.env).validate_return(self)
            return
        return super()._check_pos_dte_order_before_processing(draft)

    def _process_saved_order(self, draft):
        result = super()._process_saved_order(draft)
        if self.dte_return_target_document_id and not draft:
            if (
                self.state not in {"paid", "invoiced"}
                or len(self._get_pos_dte_documents()) != 1
            ):
                raise ValidationError(
                    _("The native POS refund did not complete its fiscal issuance.")
                )
        return result

    def _prepare_pos_dte_invoice_vals(self, vals):
        if not self.dte_return_target_document_id:
            return super()._prepare_pos_dte_invoice_vals(vals)
        PosReturnPolicyService(self.env).validate_return(self)
        document_type = self.env["l10n_latam.document.type"].search(
            [("country_id", "=", self.company_id.country_id.id), ("code", "=", "61")],
            limit=1,
        )
        if not document_type:
            raise UserError(_("Configure Chilean credit note type 61 first."))
        return {
            **vals,
            "l10n_latam_document_type_id": document_type.id,
            "dte_pos_return_order_id": self.id,
            "dte_note_target_document_id": self.dte_return_target_document_id.id,
            "dte_note_operation": self.dte_return_operation,
            "dte_note_reason": self.dte_return_reason,
        }

    def _get_invoice_lines_values(self, line_values, pos_order_line):
        vals = super()._get_invoice_lines_values(line_values, pos_order_line)
        if self.company_id.country_id.code == "CL" and (
            self.session_id.dte_pos_returns_enabled
            or self.dte_return_target_document_id
        ):
            vals["dte_pos_order_line_id"] = pos_order_line.id
            if self.dte_return_target_document_id:
                vals["dte_note_origin_line_number"] = (
                    pos_order_line.dte_return_origin_line_number
                )
        return vals

    def _create_invoice(self, move_vals):
        if self.company_id.country_id.code != "CL":
            return super()._create_invoice(move_vals)
        if self.dte_return_target_document_id:
            PosReturnPolicyService(self.env).validate_return(self)
        return super(
            PosOrder, self.with_context(_dte_pos_native_binding=_NATIVE_POS_BINDING)
        )._create_invoice(move_vals)

    def _get_pos_boleta_issuance_adapter(self):
        if self.session_id.dte_pos_returns_enabled:
            return PosTrackedBoletaIssuanceAdapter()
        return super()._get_pos_boleta_issuance_adapter()

    def _issue_pos_return_if_required(self):
        self.ensure_one()
        policy = PosReturnPolicyService(self.env)
        existing = self._get_pos_dte_documents()
        if existing:
            policy.check_actor(self.env, self.session_id)
            if (
                len(existing) != 1
                or existing.document_code != "61"
                or existing.note_pos_return_order_id != self
            ):
                raise ValidationError(
                    _("The existing fiscal document is not this POS return credit.")
                )
        else:
            policy.validate_native_result(self)
        source = (
            self.account_move
            if self.dte_return_accounting_mode == "per_order_invoice"
            else self
        )
        source = source.with_context(_dte_pos_native_binding=False)
        adapter = (
            source._get_dte_issuance_adapter("61")
            if self.account_move
            else PosReturnIssuanceAdapter()
        )
        return DteIssuanceService().issue(source, adapter, "61")

    def _issue_pos_boleta_if_required(self):
        if self.dte_return_target_document_id:
            return self._issue_pos_return_if_required()
        documents = super()._issue_pos_boleta_if_required()
        if self.session_id.dte_pos_returns_enabled and len(documents) == 1:
            PosLineMappingService(self.env).persist_mapping(self, documents)
        return documents

    @api.model
    def get_dte_return_preview(
        self, config_id, original_order_id, requested_lines, reason
    ):
        return PosReturnPolicyService(self.env).preview(
            self.env, config_id, original_order_id, requested_lines, reason
        )

    @api.model
    def get_dte_return_receipt(self, config_id, order_uuid):
        """Recover the result of an uncertain sync without performing another refund."""
        config = self.env["pos.config"].browse(config_id).exists()
        config.ensure_one()
        config.check_access("read")
        if not (
            self.env.user.has_group("point_of_sale.group_pos_user")
            and self.env.user.has_group("l10n_cl_dte_core.group_dte_user")
        ):
            raise AccessError(_("POS fiscal receipts require POS and DTE permissions."))
        if not isinstance(order_uuid, str) or len(order_uuid) > 128:
            raise ValidationError(_("Provide one persisted POS order UUID."))
        order = self.search(
            [
                ("uuid", "=", order_uuid),
                ("company_id", "=", config.company_id.id),
                ("state", "in", ["paid", "invoiced", "done"]),
            ],
            limit=1,
        )
        documents = (
            order._get_pos_dte_documents()
            if order
            else self.env["l10n_cl.electronic.document"]
        )
        if (
            len(documents) != 1
            or documents.document_code != "61"
            or documents.note_pos_return_order_id != order
            or documents.preparation_state != "queued"
        ):
            raise UserError(_("The fiscal refund is not confirmed; keep it in review."))
        return {
            "folio": documents.folio,
            "status": documents.fiscal_state,
            "original_type": documents.note_target_document_id.document_code,
            "original_folio": documents.note_target_document_id.folio,
            "original_date": read_snapshot(documents.note_target_document_id)[
                "issue_date"
            ],
            "reason": order.dte_return_reason,
        }

    @api.model
    def search_dte_return_order_ids(self, config_id, search_term, limit=30, offset=0):
        config = self.env["pos.config"].browse(config_id).exists()
        config.check_access("read")
        PosReturnPolicyService(self.env).check_actor(
            self.env, config.current_session_id
        )
        if (
            not isinstance(search_term, str)
            or len(search_term) > 128
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
            or not isinstance(offset, int)
            or offset < 0
        ):
            raise ValidationError(_("Use a bounded textual POS sale search."))
        domain = [
            ("company_id", "=", config.company_id.id),
            ("state", "in", ["paid", "done", "invoiced"]),
            ("amount_total", ">", 0),
            "|",
            ("pos_reference", "ilike", search_term),
            ("name", "ilike", search_term),
        ]
        orders = self.search(
            domain, limit=limit, offset=offset, order="date_order desc, id desc"
        )
        eligible = orders.filtered(
            lambda order: (
                len(order._get_pos_dte_documents()) == 1
                and order._get_pos_dte_documents().document_code in {"39", "41"}
                and order._get_pos_dte_documents().fiscal_state == "accepted"
            )
        )
        return {
            "ordersInfo": [
                (order.id, order.write_date)
                for order in eligible
                if order.currency_id == config.currency_id
            ],
            "totalCount": self.search_count(domain),
        }

    def action_prepare_dte_line_mapping(self):
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(
                _("Only a DTE manager can prepare legacy fiscal line mapping.")
            )
        self.check_access("read")
        documents = self._get_pos_dte_documents()
        if (
            len(documents) != 1
            or documents.document_code not in {"39", "41"}
            or documents.company_id != self.company_id
        ):
            raise ValidationError(
                _(
                    "Select one original POS boleta before preparing fiscal "
                    "line mapping."
                )
            )
        PosLineMappingService(self.env).derive_legacy_mapping(self, documents)
        return True

    @api.constrains(
        "lines", "dte_return_target_document_id", "dte_return_accounting_mode"
    )
    def _check_dte_return_shape(self):
        for order in self.filtered("dte_return_target_document_id"):
            if (
                order.company_id != order.dte_return_target_document_id.company_id
                or not order.dte_return_accounting_mode
            ):
                raise ValidationError(
                    _(
                        "The return and original fiscal evidence must share a "
                        "company and accounting policy."
                    )
                )

    @api.model
    def _load_pos_data_fields(self, config_id):
        names = super()._load_pos_data_fields(config_id)
        # An empty native field list means all fields, including the safe readonly
        # identity fields.
        return (
            names
            + [
                "dte_return_reason",
                "dte_return_target_document_id",
                "dte_return_accounting_mode",
                "dte_issuance_state",
            ]
            if names
            else names
        )

    def _has_fiscal_pos_evidence(self):
        self.ensure_one()
        self.check_access("read")
        if self.company_id.country_id.code != "CL":
            return False
        return bool(self.sudo()._get_pos_dte_documents())
