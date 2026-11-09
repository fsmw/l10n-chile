# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

_EVENT_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class DteCertificationProcessEvent(models.Model):
    _name = "l10n_cl.dte.certification.process.event"
    _description = "SII DTE Certification Audit Event"
    _order = "occurred_at desc, id desc"
    _check_company_auto = True

    process_id = fields.Many2one(
        "l10n_cl.dte.certification.process",
        required=True,
        index=True,
        check_company=True,
        ondelete="restrict",
    )
    stage_id = fields.Many2one(
        "l10n_cl.dte.certification.process.stage",
        check_company=True,
        ondelete="restrict",
    )
    company_id = fields.Many2one(
        related="process_id.company_id", store=True, index=True
    )
    event_code = fields.Char(required=True, index=True)
    occurred_at = fields.Datetime(required=True, readonly=True, index=True)
    user_id = fields.Many2one("res.users", required=True, readonly=True)
    from_state = fields.Char(readonly=True)
    to_state = fields.Char(readonly=True)
    external_reference = fields.Char(readonly=True, index=True)
    correlation_id = fields.Char(readonly=True, index=True)
    payload_digest = fields.Char(readonly=True, index=True)
    evidence_ids = fields.Many2many(
        "l10n_cl.dte.certification.evidence",
        relation="l10n_cl_cert_event_evidence_rel",
        column1="event_id",
        column2="evidence_id",
        readonly=True,
    )
    details = fields.Json(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        """Create events only from a validated workflow transition."""
        raise AccessError(
            _("Certification events can only be appended by workflow actions.")
        )

    @api.model_create_multi
    def _create_from_transition(self, vals_list):
        """Create events through the protected process transition service."""
        normalized = []
        for incoming in vals_list:
            values = incoming.copy()
            process = (
                self.env["l10n_cl.dte.certification.process"]
                .browse(values.get("process_id"))
                .exists()
            )
            if len(process) != 1:
                raise ValidationError(_("A valid certification process is required."))
            if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
                raise AccessError(
                    _("A DTE manager is required to append process events.")
                )
            stage = (
                self.env["l10n_cl.dte.certification.process.stage"]
                .browse(values.get("stage_id"))
                .exists()
            )
            if values.get("stage_id") and (
                len(stage) != 1 or stage.process_id != process
            ):
                raise ValidationError(_("The audit stage must belong to its process."))
            event_code = values.get("event_code") or ""
            if not _EVENT_CODE_RE.fullmatch(event_code):
                raise ValidationError(_("The process event code is invalid."))
            details = values.get("details") or {}
            if not isinstance(details, dict):
                raise ValidationError(_("Process event details must be a JSON object."))
            values.update(
                {
                    "user_id": values.get("user_id") or self.env.uid,
                    "occurred_at": values.get("occurred_at") or fields.Datetime.now(),
                    "details": details,
                }
            )
            normalized.append(values)
        return super(DteCertificationProcessEvent, self.sudo()).create(normalized)

    def write(self, vals):
        """Keep event history immutable."""
        raise AccessError(_("Certification events are immutable."))

    def unlink(self):
        """Keep event history immutable."""
        raise AccessError(_("Certification events are retained as audit evidence."))
