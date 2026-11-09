# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, models
from odoo.exceptions import UserError


class ElectronicDocument(models.Model):
    _inherit = "l10n_cl.electronic.document"

    def _check_signature_revision(self):
        self.ensure_one()
        lines = (
            self.env["l10n_cl.dte.certification.run.line"]
            .sudo()
            .search([("document_id", "=", self.id)])
        )
        if not lines.run_id.envelope_submission_ids:
            return super()._check_signature_revision()
        # Keep ordinary regeneration blocked after transmission. A separate
        # revision requires every retained receipt to prove complete rejection.
        result = super()._check_signature_regeneration()
        if len(lines) != 1 or not lines.run_id.can_correct_sii_envelope:
            raise UserError(_("Only a verified envelope rejection can be corrected."))
        run = lines.run_id.with_env(self.env)
        run._check_execution_access()
        for submission in run.envelope_submission_ids:
            submission._verified_schema_rejection()
        return result

    def _check_signature_regeneration(self):
        result = super()._check_signature_regeneration()
        lines = (
            self.env["l10n_cl.dte.certification.run.line"]
            .sudo()
            .search([("document_id", "=", self.id)])
        )
        if lines.run_id.envelope_submission_ids:
            raise UserError(
                _("Signatures cannot change after an SII envelope is queued.")
            )
        return result
