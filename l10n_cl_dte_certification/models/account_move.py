# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    dte_certification_run_line_id = fields.Many2one(
        "l10n_cl.dte.certification.run.line",
        readonly=True,
        copy=False,
        index=True,
        ondelete="restrict",
    )

    def action_post(self):
        if self.filtered("dte_certification_run_line_id"):
            raise UserError(
                _(
                    "Certification drafts are evidence for SII testing and "
                    "cannot be posted. Use the certification workflow."
                )
            )
        return super().action_post()
