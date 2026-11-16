# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

BINDING = "transport_request_artifact_id"


class ElectronicSubmissionProduction(models.Model):
    _inherit = "l10n_cl.electronic.submission"

    transport_request_artifact_id = fields.Many2one(
        "l10n_cl.electronic.artifact",
        string="Signed Transport Envelope",
        readonly=True,
        copy=False,
        index=True,
        check_company=True,
        ondelete="restrict",
    )
    _sql_constraints = [
        (
            "submission_transport_request_uniq",
            "unique(transport_request_artifact_id)",
            "A transport envelope can belong to only one submission.",
        )
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if any(vals.get(BINDING) for vals in vals_list) or self.env.context.get(
            "default_" + BINDING
        ):
            raise UserError(
                _("Prepare the transport envelope through the submission workflow.")
            )
        return super().create([{**vals, BINDING: False} for vals in vals_list])

    def _lock_production_chain(self):
        self.ensure_one()
        self.document_id._lock_fiscal_chain()
        self.flush_recordset()
        self.env.cr.execute(
            "SELECT id FROM l10n_cl_electronic_submission WHERE id = %s FOR UPDATE",
            [self.id],
        )
        self.invalidate_recordset()

    def _check_request_preparation(self):
        if (
            self.environment != "production"
            or self.document_id.sii_environment != "production"
            or self.company_id.dte_sii_environment != "production"
            or self.state != "pending"
            or self.attempt_ids.filtered(lambda attempt: attempt.operation == "send")
        ):
            raise UserError(
                _(
                    "Only pending Production submissions without a send attempt "
                    "can bind an envelope."
                )
            )

    def _write_internal(self, values):
        """Dormant binding primitive; caller must verify fiscal envelope content."""
        if BINDING not in values:
            return super()._write_internal(values)
        if not self.env.su:
            raise AccessError(
                _("Transport binding requires an authorized internal workflow.")
            )
        self._lock_production_chain()
        self._check_request_preparation()
        if (
            set(values) != {BINDING}
            or self.transport_request_artifact_id
            or not values[BINDING]
        ):
            raise UserError(
                _("A transport envelope must be bound once, without other changes.")
            )
        artifact = (
            self.env["l10n_cl.electronic.artifact"].browse(values[BINDING]).exists()
        )
        if (
            len(artifact) != 1
            or artifact.document_id != self.document_id
            or artifact.company_id != self.company_id
            or artifact.role != "sii_request"
        ):
            raise ValidationError(
                _("The transport envelope must be request evidence for this DTE.")
            )
        artifact.with_context(bin_size=False).action_verify_integrity()
        return super()._write_internal(values)
