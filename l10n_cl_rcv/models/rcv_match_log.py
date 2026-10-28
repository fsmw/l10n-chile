# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import hashlib
import json

from psycopg2 import IntegrityError

from odoo import _, fields, models
from odoo.exceptions import UserError


class RcvMatchLog(models.Model):
    _name = "l10n.cl.rcv.match.log"
    _description = "SII RCV reconciliation audit"
    _order = "create_date desc, id desc"
    _check_company_auto = True

    document_id = fields.Many2one(
        "l10n.cl.rcv.document", required=True, index=True, ondelete="cascade"
    )
    company_id = fields.Many2one(
        related="document_id.company_id", store=True, index=True
    )
    previous_move_id = fields.Many2one("account.move", ondelete="set null")
    move_id = fields.Many2one("account.move", ondelete="set null")
    user_id = fields.Many2one(
        "res.users", required=True, default=lambda self: self.env.user
    )
    reason = fields.Text(required=True)
    event_type = fields.Selection(
        [("manual", "Manual"), ("automatic", "Automático")],
        string="Tipo de evento",
        required=True,
        default="manual",
        index=True,
    )
    decision_data = fields.Text(string="Evidencia de decisión", copy=False)
    decision_hash = fields.Char(string="Huella de decisión", copy=False, index=True)
    rule_version = fields.Char(string="Versión de regla", copy=False)

    _sql_constraints = [
        (
            "automatic_decision_hash_uniq",
            "unique(document_id, event_type, decision_hash)",
            "Esta decisión automática ya fue registrada.",
        )
    ]

    def action_confirm(self):
        if self.filtered(lambda log: log.event_type != "manual"):
            raise UserError(
                _("Los eventos automáticos de conciliación son solo de auditoría.")
            )
        for log in self:
            log.document_id.write(
                {"move_id": log.move_id.id, "reconciliation_state": "review"}
            )

    def _log_automatic_decision(self, document, move, decision, evidence):
        """Create one audit event per distinct automatic decision and evidence."""
        payload = {
            "decision": decision,
            "evidence": evidence or {},
            "move_id": move.id if move else False,
            "reason_code": document.reconciliation_reason_code or False,
            "state": document.reconciliation_state,
            "version": "rcv-fiscal-v1",
        }
        serialized = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, default=str
        )
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        if self.search_count(
            [
                ("document_id", "=", document.id),
                ("event_type", "=", "automatic"),
                ("decision_hash", "=", digest),
            ]
        ):
            return self.browse()
        values = {
            "document_id": document.id,
            "previous_move_id": (evidence or {}).get("previous_move_id") or False,
            "move_id": move.id if move else False,
            "event_type": "automatic",
            "decision_data": serialized,
            "decision_hash": digest,
            "rule_version": payload["version"],
            "reason": document.automation_error
            or _("Evaluación automática de conciliación RCV."),
        }
        try:
            with self.env.cr.savepoint():
                return self.create(values)
        except IntegrityError:
            existing = self.search(
                [
                    ("document_id", "=", document.id),
                    ("event_type", "=", "automatic"),
                    ("decision_hash", "=", digest),
                ],
                limit=1,
            )
            if existing:
                return existing
            raise
