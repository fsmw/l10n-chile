# Copyright 2026 Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, models


class L10nLatamIdentificationType(models.Model):
    _name = "l10n_latam.identification.type"
    _inherit = ["l10n_latam.identification.type", "pos.load.mixin"]

    @api.model
    def _load_pos_data_fields(self, config_id):
        config = self.env["pos.config"].browse(config_id)
        if config.company_id.account_fiscal_country_id.code == "CL":
            return ["name"]
        return super()._load_pos_data_fields(config_id)

    @api.model
    def _load_pos_data_domain(self, data):
        company = self.env.company
        if data.get("pos.config", {}).get("data"):
            company_id = data["pos.config"]["data"][0].get("company_id")
            company = self.env["res.company"].browse(company_id)
        if company.account_fiscal_country_id.code == "CL":
            return False
        return super()._load_pos_data_domain(data)
