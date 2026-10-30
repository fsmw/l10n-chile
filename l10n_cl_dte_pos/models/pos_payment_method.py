# Copyright 2026 Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    is_card_payment = fields.Boolean(
        string="Pago con tarjeta",
        help=(
            "Solicita el número de voucher en la caja chilena para este medio "
            "de pago. Actívelo sólo para pagos con tarjeta; un diario bancario "
            "también puede representar una transferencia u otro medio."
        ),
    )
    l10n_cl_dte_company_country_code = fields.Char(
        related="company_id.country_id.code"
    )

    @api.model
    def _load_pos_data_fields(self, config_id):
        values = super()._load_pos_data_fields(config_id)
        config = self.env["pos.config"].browse(config_id)
        if config.company_id.account_fiscal_country_id.code == "CL":
            values.append("is_card_payment")
        return values
