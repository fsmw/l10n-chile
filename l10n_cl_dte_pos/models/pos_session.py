# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import json
from decimal import Decimal, InvalidOperation

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PosSession(models.Model):
    _inherit = "pos.session"

    dte_pos_accounting_mode = fields.Selection(
        selection=[
            ("per_order_invoice", "Invoice per POS order (standard accounting)"),
            ("session_summary", "DTE per order, accounting at session close"),
        ],
        string="POS DTE Accounting Mode",
        required=True,
        readonly=True,
        index=True,
        copy=False,
    )
    dte_order_count = fields.Integer(compute="_compute_dte_session_summary")
    dte_pending_order_count = fields.Integer(compute="_compute_dte_session_summary")
    dte_pos_total = fields.Monetary(compute="_compute_dte_session_summary")
    dte_readiness = fields.Selection(
        [("ready", "Ready"), ("pending", "Pending")],
        compute="_compute_dte_session_summary",
    )

    @api.depends(
        "order_ids.state",
        "order_ids.amount_total",
        "order_ids.dte_document_ids.preparation_state",
    )
    def _compute_dte_session_summary(self):
        for session in self:
            orders = session.order_ids.filtered(
                lambda order: order.company_id.country_id.code == "CL"
                and order.state in {"paid", "done", "invoiced"}
            )
            complete = orders.filtered(
                lambda order: len(order.dte_document_ids) == 1
                and order.dte_document_ids.preparation_state == "queued"
            )
            session.dte_order_count = len(complete)
            session.dte_pending_order_count = len(orders) - len(complete)
            session.dte_pos_total = sum(complete.mapped("amount_total"))
            session.dte_readiness = (
                "ready" if len(complete) == len(orders) else "pending"
            )

    @api.model
    def _load_pos_data_fields(self, config_id):
        return super()._load_pos_data_fields(config_id) + ["dte_pos_accounting_mode"]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("dte_pos_accounting_mode"):
                config = self.env["pos.config"].browse(vals.get("config_id"))
                company_id = vals.get("company_id") or config.company_id.id
                company = self.env["res.company"].sudo().browse(company_id)
                vals["dte_pos_accounting_mode"] = (
                    company.dte_pos_accounting_mode or "per_order_invoice"
                )
        return super().create(vals_list)

    def action_pos_session_open(self):
        """Capture the company policy when the session opens."""
        for session in self.filtered(lambda item: item.state == "opening_control"):
            session.write(
                {
                    "dte_pos_accounting_mode": (
                        session.company_id.sudo().dte_pos_accounting_mode
                    )
                }
            )
        return super().action_pos_session_open()

    def action_pos_session_closing_control(
        self,
        balancing_account=False,
        amount_to_balance=0,
        bank_payment_method_diffs=None,
    ):
        """Validate POS DTE completeness and reconciliation before closing."""
        for session in self:
            if session.dte_pos_accounting_mode != "session_summary":
                continue
            orders = session.order_ids.filtered(
                lambda order: order.state in {"paid", "done", "invoiced"}
                and order.company_id.country_id.code == "CL"
            )
            for order in orders:
                documents = order.dte_document_ids
                if len(documents) != 1 or documents.preparation_state != "queued":
                    raise UserError(
                        _(
                            "The POS session cannot close because order %s does "
                            "not have exactly one prepared DTE."
                        )
                        % order.pos_reference
                    )
                snapshots = documents.artifact_ids.filtered(
                    lambda artifact: artifact.role == "source_snapshot"
                )
                if len(snapshots) != 1 or not documents.payload_digest:
                    raise UserError(
                        _("The DTE snapshot for order %s is missing.")
                        % order.pos_reference
                    )
                snapshots.sudo().action_verify_integrity()
                try:
                    payload = json.loads(
                        base64.b64decode(snapshots.sudo().content, validate=True)
                    )
                    dte_total = Decimal(payload["totals"]["total"])
                    order_total = Decimal(str(order.amount_total))
                except (ValueError, TypeError, KeyError, InvalidOperation) as error:
                    raise UserError(
                        _("The DTE snapshot for order %s is invalid.")
                        % order.pos_reference
                    ) from error
                if dte_total != order_total:
                    raise UserError(
                        _(
                            "The DTE total for order %(order)s does not match its "
                            "POS total."
                        )
                        % {"order": order.pos_reference}
                    )
        return super().action_pos_session_closing_control(
            balancing_account=balancing_account,
            amount_to_balance=amount_to_balance,
            bank_payment_method_diffs=bank_payment_method_diffs,
        )
