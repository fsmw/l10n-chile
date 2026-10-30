# Copyright 2026 Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    @api.model
    def _load_pos_data_fields(self, config_id):
        values = super()._load_pos_data_fields(config_id)
        config = self.env["pos.config"].browse(config_id)
        if config.company_id.account_fiscal_country_id.code == "CL":
            values += [
                "l10n_latam_identification_type_id",
                "l10n_cl_sii_taxpayer_type",
                "l10n_cl_activity_description",
                "l10n_cl_dte_email",
                "street2",
            ]
        return values
