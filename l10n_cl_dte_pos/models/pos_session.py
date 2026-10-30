# Copyright 2026 Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, models


class PosSession(models.Model):
    _inherit = "pos.session"

    @api.model
    def _load_pos_data_models(self, config_id):
        models_to_load = super()._load_pos_data_models(config_id)
        config = self.env["pos.config"].browse(config_id)
        if config.company_id.account_fiscal_country_id.code == "CL":
            models_to_load += [
                "l10n_latam.identification.type",
            ]
        return models_to_load

    def _load_pos_data(self, data):
        result = super()._load_pos_data(data)
        session_data = result["data"][0]
        company = self.company_id
        if company.account_fiscal_country_id.code == "CL":
            session_data["_consumidor_final_anonimo_id"] = self.env.ref(
                "l10n_cl.par_cfa"
            ).id
            fiscal_data = company.sudo().read(
                [
                    "vat",
                    "l10n_cl_dte_resolution_number",
                    "l10n_cl_dte_resolution_date",
                ],
                load=False,
            )[0]
            session_data["_l10n_cl_company_vat"] = fiscal_data["vat"]
            session_data["_l10n_cl_dte_resolution_number"] = fiscal_data[
                "l10n_cl_dte_resolution_number"
            ]
            session_data["_l10n_cl_dte_resolution_date"] = fiscal_data[
                "l10n_cl_dte_resolution_date"
            ]
        return result
