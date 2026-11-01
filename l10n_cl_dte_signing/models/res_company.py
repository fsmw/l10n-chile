from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    dte_certificate_id = fields.Many2one(
        "certificate.certificate",
        string="DTE Signing Certificate",
        check_company=True,
        groups="l10n_cl_dte_core.group_dte_manager",
    )
