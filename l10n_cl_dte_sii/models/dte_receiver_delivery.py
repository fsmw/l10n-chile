# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class L10nClDteReceiverDelivery(models.Model):
    _name = "l10n_cl.dte.receiver.delivery"
    _description = "Envío de DTE al receptor"
    _order = "requested_at desc, id desc"
    _check_company_auto = True

    move_id = fields.Many2one(
        "account.move",
        required=True,
        readonly=True,
        ondelete="restrict",
        index=True,
        check_company=True,
    )
    company_id = fields.Many2one(
        related="move_id.company_id", store=True, readonly=True, index=True
    )
    recipient_email = fields.Char(required=True, readonly=True)
    requested_at = fields.Datetime(required=True, readonly=True)
    actor_id = fields.Many2one("res.users", required=True, readonly=True)
    envelope_attachment_id = fields.Many2one(
        "ir.attachment", required=True, readonly=True, ondelete="restrict"
    )
    mail_id = fields.Many2one(
        "mail.mail", required=True, readonly=True, ondelete="restrict"
    )
    state = fields.Selection(
        [
            ("outgoing", "En cola"),
            ("sent", "Enviado"),
            ("exception", "Error de entrega"),
            ("cancel", "Cancelado"),
        ],
        compute="_compute_state",
        store=True,
        readonly=True,
    )

    @api.depends("mail_id.state")
    def _compute_state(self):
        for delivery in self:
            state = delivery.mail_id.state
            delivery.state = (
                state
                if state in {"outgoing", "sent", "exception", "cancel"}
                else "outgoing"
            )

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get("_l10n_cl_create_receiver_delivery"):
            raise AccessError(
                _("Los envíos de DTE sólo se crean desde la acción autorizada.")
            )
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError(_("La evidencia de envío de DTE es inmutable."))

    def unlink(self):
        raise AccessError(_("La evidencia de envío de DTE no se puede eliminar."))
