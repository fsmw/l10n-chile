# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, models
from odoo.exceptions import UserError, ValidationError


class ElectronicDocument(models.Model):
    _inherit = "l10n_cl.electronic.document"

    def _get_transition_details(self, target_state):
        domain, current, transitions, field = super()._get_transition_details(
            target_state
        )
        if (
            self.env.su
            and self.sii_environment == "production"
            and domain == "fiscal"
            and current == "unknown"
            and target_state == "rejected"
        ):
            transitions = {**transitions, current: transitions[current] | {"rejected"}}
        return domain, current, transitions, field

    def _get_transition_artifact_role(
        self, target_state, state_domain, artifact, correlation_key
    ):
        if (
            state_domain == "fiscal"
            and target_state == "rejected"
            and (self.fiscal_state == "unknown" or artifact.role == "sii_response")
        ):
            self._verify_production_upload_rejection(artifact, correlation_key)
            return "sii_response"
        return super()._get_transition_artifact_role(
            target_state, state_domain, artifact, correlation_key
        )

    def _verify_production_upload_rejection(self, artifact, correlation_key):
        submission = self.env["l10n_cl.electronic.submission"].search(
            [("document_id", "=", self.id)]
        )
        attempts = submission._dispatched_send_attempts()
        if (
            not self.env.su
            or len(submission) != 1
            or submission.environment != "production"
            or self.sii_environment != "production"
            or self.company_id.dte_sii_environment != "production"
            or self.preparation_state != "queued"
            or self.fiscal_state != "unknown"
            or submission.state != "sending"
            or submission.track_id
            or correlation_key != submission.idempotency_key
            or len(attempts) != 1
            or attempts.state != "completed"
            or not attempts.completed_at
            or attempts.request_digest
            != submission.transport_request_artifact_id.sha256
            or len(artifact) != 1
            or artifact.document_id != self
            or artifact.company_id != self.company_id
            or artifact.role != "sii_response"
            or attempts.response_digest != artifact.sha256
        ):
            raise ValidationError(
                _("A verified Production upload rejection is required.")
            )
        artifact.with_context(bin_size=False).action_verify_integrity()
        request = submission._verify_production_request()
        if (
            submission._parse_production_receipt(artifact, request)["state"]
            != "rejected"
        ):
            raise ValidationError(
                _("The retained upload receipt must reject this DTE.")
            )

    def _lock_fiscal_chain(self):
        """Note extensions must lock their root chain before this default lock."""
        self.flush_recordset()
        self.env.cr.execute(
            "SELECT id FROM l10n_cl_electronic_document "
            "WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
            [self.ids],
        )
        self.invalidate_recordset()

    def _check_signature_regeneration(self):
        result = super()._check_signature_regeneration()
        if (
            self.env["l10n_cl.electronic.submission"]
            .sudo()
            .search_count([("document_id", "=", self.id)])
        ):
            raise UserError(_("DTE with an SII submission cannot be regenerated."))
        return result
