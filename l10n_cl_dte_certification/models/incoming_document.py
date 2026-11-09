# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class IncomingDte(models.Model):
    _inherit = "l10n_cl.incoming.dte"

    certification_stage_id = fields.Many2one(
        "l10n_cl.dte.certification.process.stage",
        check_company=True,
        index=True,
        readonly=True,
        ondelete="restrict",
    )

    def action_link_to_active_certification_stage(self):
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(
                _("Only a DTE manager can link incoming documents to certification.")
            )
        if self.certification_stage_id:
            raise UserError(_("This incoming DTE is already linked to a stage."))
        if self.sii_environment != "certification":
            raise ValidationError(
                _(
                    "Only Certification environment documents can be linked to "
                    "this process."
                )
            )
        processes = self.env["l10n_cl.dte.certification.process"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("state", "in", ["in_progress", "waiting_sii", "blocked"]),
                ("active", "=", True),
                ("current_stage_code", "=", "information_exchange"),
            ],
            limit=2,
        )
        if len(processes) != 1:
            raise UserError(
                _(
                    "Exactly one active certification process must be at "
                    "Information Exchange."
                )
            )
        stage = processes.stage_ids.filtered(
            lambda item: (
                item.code == "information_exchange"
                and item.state in {"in_progress", "blocked"}
            )
        )
        if len(stage) != 1:
            raise UserError(_("The current Information Exchange stage is unavailable."))
        self._link_to_certification_stage(stage)
        return self.action_open()

    def _link_to_certification_stage(self, stage):
        self.ensure_one()
        if (
            stage.code != "information_exchange"
            or stage.company_id != self.company_id
            or self.sii_environment != "certification"
        ):
            raise ValidationError(
                _(
                    "The stage and incoming DTE must belong to the same "
                    "Certification company."
                )
            )
        if self.certification_stage_id and self.certification_stage_id != stage:
            raise UserError(
                _("An incoming DTE cannot be moved between certification stages.")
            )
        if not self.certification_stage_id:
            self._write_internal({"certification_stage_id": stage.id})
            self._create_event(
                "linked_to_certification_stage",
                self.state,
                self.state,
                self.xml_sha256,
            )
            stage.process_id._record_event(
                "incoming_dte_linked",
                stage=stage,
                details={"incoming_dte_sha256": self.xml_sha256},
            )


class DteCertificationProcessStage(models.Model):
    _inherit = "l10n_cl.dte.certification.process.stage"

    incoming_document_ids = fields.One2many(
        "l10n_cl.incoming.dte", "certification_stage_id", readonly=True
    )

    def _check_incoming_exchange_readiness(self):
        self.ensure_one()
        if self.code != "information_exchange":
            return []
        diagnostics = []
        documents = self.incoming_document_ids
        if not documents:
            return [{"code": "incoming_documents_required"}]
        for document in documents:
            if (
                document.company_id != self.company_id
                or document.sii_environment != "certification"
            ):
                diagnostics.append(
                    {"code": "incoming_document_company_or_environment_mismatch"}
                )
            if document.xml_validation_state != "verified":
                diagnostics.append(
                    {
                        "code": "incoming_document_cryptographic_verification_required",
                        "incoming_dte_sha256": document.xml_sha256,
                    }
                )
            if not document.response_ids.filtered(
                lambda response: response.state in {"submitted", "accepted"}
            ):
                diagnostics.append(
                    {
                        "code": "incoming_response_submission_required",
                        "incoming_dte_sha256": document.xml_sha256,
                    }
                )
        return diagnostics

    def action_run_preflight(self):
        result = super().action_run_preflight()
        for stage in self.filtered(lambda item: item.code == "information_exchange"):
            diagnostics = stage._check_incoming_exchange_readiness()
            if diagnostics:
                stage._write_internal(
                    {
                        "state": "blocked",
                        "diagnostics": (stage.diagnostics or []) + diagnostics,
                    }
                )
                stage.process_id._record_event(
                    "incoming_exchange_preflight_blocked",
                    stage=stage,
                    to_state="blocked",
                    details={"diagnostic_count": len(diagnostics)},
                )
        return result
