# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, models

GUIDE_CODE = "52"
GUIDE_PRINTABLE_STATES = {"signed", "queued"}


class DteReportSample(models.Model):
    _inherit = "l10n_cl.dte.report.sample"

    @api.model
    def _supported_document_codes(self):
        return super()._supported_document_codes() | {GUIDE_CODE}

    @api.model
    def _printable_states(self, document_code):
        """Let a signed guide be printed before it is sent to SII."""
        if document_code == GUIDE_CODE:
            return GUIDE_PRINTABLE_STATES
        return super()._printable_states(document_code)

    def _print_report_action(self, document_code):
        if document_code == GUIDE_CODE:
            return self.env.ref("l10n_cl_dte_dispatch_report.action_dispatch_print")
        return super()._print_report_action(document_code)
