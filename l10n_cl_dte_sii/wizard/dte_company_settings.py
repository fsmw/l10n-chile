# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.res_company import DTE_ENVIRONMENTS


class L10nClDteCompanySettings(models.TransientModel):
    _name = "l10n_cl.dte.company.settings"
    _description = "Configuración del emisor DTE SII"

    company_id = fields.Many2one(
        "res.company",
        string="Compañía",
        required=True,
        readonly=True,
        default=lambda self: self.env.company,
    )
    l10n_cl_dte_environment = fields.Selection(
        DTE_ENVIRONMENTS, string="Ambiente SII", required=True
    )
    l10n_cl_dte_email = fields.Char(string="Correo DTE")
    l10n_cl_dte_activity = fields.Char(string="Giro")
    l10n_cl_dte_activity_code = fields.Char(string="Código de actividad económica SII")
    l10n_cl_dte_activity_ids = fields.Many2many(
        "l10n_cl.dte.activity", string="Actividades económicas SII"
    )
    l10n_cl_dte_office_code = fields.Char(string="Código de oficina SII")
    l10n_cl_dte_resolution_number = fields.Char(string="Número de resolución SII")
    l10n_cl_dte_resolution_date = fields.Date(string="Fecha de resolución SII")
    certificate_option = fields.Selection(
        selection=lambda self: self._selection_certificate_options(),
        string="Certificado de firma DTE",
    )

    @api.model
    def _selection_certificate_options(self):
        company_id = self.env.context.get("default_company_id") or self.env.company.id
        certificates = (
            self.env["certificate.certificate"]
            .sudo()
            .search(
                [
                    ("company_id", "=", company_id),
                    ("active", "=", True),
                    ("is_valid", "=", True),
                ],
                order="name, id",
            )
        )
        return [
            (str(certificate.id), certificate.display_name)
            for certificate in certificates
        ]

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        company_id = (
            values.get("company_id")
            or self.env.context.get("default_company_id")
            or self.env.company.id
        )
        company = self.env["res.company"].browse(company_id)
        if company not in self.env.companies:
            raise UserError(_("You cannot configure another company's DTE data."))
        config_fields = (
            "l10n_cl_dte_environment",
            "l10n_cl_dte_email",
            "l10n_cl_dte_activity",
            "l10n_cl_dte_activity_code",
            "l10n_cl_dte_office_code",
            "l10n_cl_dte_resolution_number",
            "l10n_cl_dte_resolution_date",
        )
        for field_name in config_fields:
            if field_name in fields_list:
                values[field_name] = company[field_name]
        if "l10n_cl_dte_activity_ids" in fields_list:
            values["l10n_cl_dte_activity_ids"] = [
                (6, 0, company.l10n_cl_dte_activity_ids.ids)
            ]
        if "certificate_option" in fields_list:
            certificate = company.sudo().l10n_cl_dte_certificate_id
            values["certificate_option"] = str(certificate.id) if certificate else False
        return values

    def action_apply(self):
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_sii.group_dte_manager"):
            raise UserError(_("Only a DTE manager can configure SII company data."))
        company = self.company_id
        if company not in self.env.companies:
            raise UserError(_("You cannot configure another company's DTE data."))
        certificate = False
        if self.certificate_option:
            try:
                certificate_id = int(self.certificate_option)
            except (TypeError, ValueError) as error:
                raise UserError(_("Choose a valid company certificate.")) from error
            certificate = (
                self.env["certificate.certificate"].sudo().browse(certificate_id)
            )
            if (
                not certificate.exists()
                or certificate.company_id != company
                or not certificate.active
                or not certificate.is_valid
            ):
                raise UserError(_("Choose an active certificate for this company."))
        company.sudo().write(
            {
                "l10n_cl_dte_environment": self.l10n_cl_dte_environment,
                "l10n_cl_dte_email": self.l10n_cl_dte_email,
                "l10n_cl_dte_activity": self.l10n_cl_dte_activity,
                "l10n_cl_dte_activity_code": self.l10n_cl_dte_activity_code,
                "l10n_cl_dte_activity_ids": [
                    (6, 0, self.l10n_cl_dte_activity_ids.ids)
                ],
                "l10n_cl_dte_office_code": self.l10n_cl_dte_office_code,
                "l10n_cl_dte_resolution_number": self.l10n_cl_dte_resolution_number,
                "l10n_cl_dte_resolution_date": self.l10n_cl_dte_resolution_date,
                "l10n_cl_dte_certificate_id": certificate.id if certificate else False,
            }
        )
        return {"type": "ir.actions.act_window_close"}
