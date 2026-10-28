# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = "res.company"

    l10n_cl_rcv_automation_enabled = fields.Boolean(
        string="Crear borradores RCV automáticamente",
        help=(
            "Read purchase RCV daily and create vendor bill drafts for missing "
            "documents in Registro. Enable only after unattended SII detail "
            "retrieval has been verified in QA."
        ),
    )
    l10n_cl_rcv_service_user_id = fields.Many2one(
        "res.users",
        string="Usuario de automatización RCV",
        help=(
            "User whose Chilean digital signature is used for scheduled RCV "
            "reads. The user must have access to this company."
        ),
    )

    @api.constrains("l10n_cl_rcv_automation_enabled", "l10n_cl_rcv_service_user_id")
    def _check_rcv_automation_user(self):
        for company in self:
            if not company.l10n_cl_rcv_automation_enabled:
                continue
            user = company.l10n_cl_rcv_service_user_id
            if not user:
                raise ValidationError(
                    _(
                        "Seleccione el usuario de firma antes de activar la "
                        "automatización RCV."
                    )
                )
            if company not in user.company_ids:
                raise ValidationError(
                    _(
                        "El usuario de automatización debe tener acceso a la compañía "
                        "configurada."
                    )
                )
