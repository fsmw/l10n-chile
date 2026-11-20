# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hashlib
import json

from psycopg2 import IntegrityError

from odoo import fields, models

RULE_VERSION = "l10n_cl_dte_rcv-1"


class RcvMatchLog(models.Model):
    """Audit trail of the reconciliation decisions of an RCV document.

    Automatic decisions are recorded once per distinct outcome and evidence;
    manual links and unlinks are always recorded with their reason.
    """

    _name = "l10n_cl.dte.rcv.match.log"
    _description = "SII RCV Reconciliation Log"
    _order = "create_date desc, id desc"
    _check_company_auto = True

    document_id = fields.Many2one(
        "l10n_cl.dte.rcv.document",
        required=True,
        index=True,
        ondelete="cascade",
        readonly=True,
    )
    company_id = fields.Many2one(
        related="document_id.company_id", store=True, index=True
    )
    event_type = fields.Selection(
        [
            ("automatic", "Automatic"),
            ("manual_link", "Manual Link"),
            ("manual_unlink", "Manual Unlink"),
        ],
        required=True,
        index=True,
        readonly=True,
    )
    previous_move_id = fields.Many2one(
        "account.move", string="Previous Entry", ondelete="set null", readonly=True
    )
    move_id = fields.Many2one(
        "account.move", string="Journal Entry", ondelete="set null", readonly=True
    )
    user_id = fields.Many2one(
        "res.users", required=True, default=lambda self: self.env.user, readonly=True
    )
    reconciliation_state = fields.Char(readonly=True)
    reconciliation_reason_code = fields.Char(string="Reason Code", readonly=True)
    reason = fields.Text(required=True, readonly=True)
    decision_data = fields.Text(string="Decision Evidence", readonly=True)
    decision_hash = fields.Char(readonly=True, index=True)
    rule_version = fields.Char(readonly=True)

    _sql_constraints = [
        (
            "automatic_decision_hash_uniq",
            "unique(document_id, event_type, decision_hash)",
            "This automatic decision is already recorded.",
        )
    ]

    def _log_automatic_decision(self, document, move, evidence):
        """Record one automatic decision unless the same one is recorded."""
        payload = {
            "state": document.reconciliation_state,
            "reason_code": document.reconciliation_reason_code or False,
            "tax_comparison_state": document.tax_comparison_state,
            "tax_allocation_state": document.tax_allocation_state,
            "move_id": move.id if move else False,
            "linked_move_id": document.move_id.id,
            "evidence": evidence or {},
            "version": RULE_VERSION,
        }
        serialized = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, default=str
        )
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        domain = [
            ("document_id", "=", document.id),
            ("event_type", "=", "automatic"),
            ("decision_hash", "=", digest),
        ]
        existing = self.search(domain, limit=1)
        if existing:
            return existing
        values = {
            "document_id": document.id,
            "event_type": "automatic",
            "move_id": move.id if move else False,
            "reconciliation_state": document.reconciliation_state,
            "reconciliation_reason_code": document.reconciliation_reason_code,
            "reason": document.reconciliation_message
            or self.env._("Automatic RCV reconciliation."),
            "decision_data": serialized,
            "decision_hash": digest,
            "rule_version": RULE_VERSION,
        }
        try:
            with self.env.cr.savepoint():
                return self.create(values)
        except IntegrityError:
            return self.search(domain, limit=1)
