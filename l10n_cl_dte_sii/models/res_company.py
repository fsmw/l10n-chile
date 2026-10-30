# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.exceptions import UserError

DTE_ENVIRONMENTS = [
    ("SIIDEMO", "Demostración"),
    ("SIITEST", "Certificación"),
    ("SII", "Producción"),
]


class ResCompany(models.Model):
    _inherit = "res.company"

    l10n_cl_dte_environment = fields.Selection(
        DTE_ENVIRONMENTS,
        string="Ambiente SII",
        default="SIIDEMO",
        groups="l10n_cl_dte_sii.group_dte_manager",
    )
    l10n_cl_dte_email = fields.Char(
        string="Correo DTE", groups="l10n_cl_dte_sii.group_dte_manager"
    )
    l10n_cl_dte_activity = fields.Char(
        string="Giro", groups="l10n_cl_dte_sii.group_dte_manager"
    )
    l10n_cl_dte_office_code = fields.Char(
        string="Código de oficina SII", groups="l10n_cl_dte_sii.group_dte_manager"
    )
    l10n_cl_dte_resolution_number = fields.Char(
        string="Número de resolución SII",
        groups="l10n_cl_dte_sii.group_dte_manager",
    )
    l10n_cl_dte_resolution_date = fields.Date(
        string="Fecha de resolución SII",
        groups="l10n_cl_dte_sii.group_dte_manager",
    )
    l10n_cl_dte_activity_code = fields.Char(
        string="Código de actividad económica SII",
        groups="l10n_cl_dte_sii.group_dte_manager",
    )
    l10n_cl_dte_certificate_id = fields.Many2one(
        "certificate.certificate",
        string="Certificado asignado",
        check_company=True,
        domain="[('company_id', '=', id), ('active', '=', True)]",
        groups="l10n_cl_dte_sii.group_dte_manager",
    )
    l10n_cl_dte_certificate_status = fields.Char(
        string="Certificado de firma DTE",
        compute="_compute_l10n_cl_dte_certificate_status",
        compute_sudo=True,
        groups="l10n_cl_dte_sii.group_dte_manager",
    )

    def _compute_l10n_cl_dte_certificate_status(self):
        for company in self:
            certificate = company.sudo().l10n_cl_dte_certificate_id
            company.l10n_cl_dte_certificate_status = (
                certificate.display_name if certificate else _("Sin asignar")
            )

    def _get_digital_signature(self, user_id=None):
        self.ensure_one()
        certificate = self.sudo().l10n_cl_dte_certificate_id
        if not certificate or not certificate.is_valid:
            raise UserError(
                _("Select a valid SII signing certificate for this company.")
            )
        if certificate.company_id != self:
            raise UserError(_("The signing certificate belongs to another company."))
        if certificate.user_id and certificate.user_id.id != user_id:
            raise UserError(
                _("The selected certificate is restricted to another user.")
            )
        if not certificate.private_key_id:
            raise UserError(_("The selected certificate has no linked private key."))
        if not certificate.subject_serial_number:
            raise UserError(
                _("The certificate has no RUT in its subject serial number.")
            )
        return certificate
