# Copyright 2026 Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, models
from odoo.exceptions import UserError


class PosConfig(models.Model):
    _inherit = "pos.config"

    def open_ui(self):
        self._l10n_cl_validate_dte_setup()
        return super().open_ui()

    def _l10n_cl_validate_dte_setup(self):
        for config in self:
            if config.company_id.account_fiscal_country_id.code != "CL":
                continue
            anonymous_partner = self.env.ref(
                "l10n_cl.par_cfa", raise_if_not_found=False
            )
            if not anonymous_partner or not anonymous_partner.active:
                raise UserError(
                    _(
                        "El contacto 'Consumidor Final Anónimo' debe existir "
                        "y estar activo."
                    )
                )
            journal = config.invoice_journal_id
            if not journal or journal.company_id != config.company_id:
                raise UserError(
                    _(
                        "Configure un diario de facturas de venta para esta "
                        "compañía POS."
                    )
                )
            if (
                not journal.l10n_latam_use_documents
                or journal.l10n_cl_dte_point_of_sale != "online"
                or not journal.l10n_cl_dte_sucursal
                or not journal.l10n_cl_dte_office_number
            ):
                raise UserError(
                    _(
                        "El diario de facturas POS debe usar documentos electrónicos, "
                        "un punto de venta SII en línea, sucursal y oficina."
                    )
                )
        return True

    def get_limited_partners_loading(self):
        partner_ids = super().get_limited_partners_loading()
        for _config in self.filtered(
            lambda record: record.company_id.account_fiscal_country_id.code == "CL"
        ):
            anonymous_partner = self.env.ref("l10n_cl.par_cfa")
            if (anonymous_partner.id,) not in partner_ids:
                partner_ids.append((anonymous_partner.id,))
        return partner_ids
