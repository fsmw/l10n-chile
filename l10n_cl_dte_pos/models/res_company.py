# Copyright 2026 Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, models


class ResCompany(models.Model):
    _inherit = "res.company"

    @api.model
    def _load_pos_data_fields(self, config_id):
        values = super()._load_pos_data_fields(config_id)
        config = self.env["pos.config"].browse(config_id)
        if config.company_id.account_fiscal_country_id.code == "CL":
            values += [
                "l10n_cl_dte_resolution_number",
                "l10n_cl_dte_resolution_date",
            ]
        return values

    @api.model
    def _load_pos_data(self, data):
        config_id = data["pos.config"]["data"][0]["id"]
        config = self.env["pos.config"].browse(config_id).exists()
        if not config:
            return super()._load_pos_data(data)
        config.check_access("read")
        if config.company_id.account_fiscal_country_id.code != "CL":
            return super()._load_pos_data(data)
        # These two fields are printed on the tax receipt. They are public
        # fiscal metadata, but the core configuration is restricted to DTE
        # managers, so expose only the POS field whitelist through sudo.
        return super(ResCompany, self.sudo())._load_pos_data(data)
