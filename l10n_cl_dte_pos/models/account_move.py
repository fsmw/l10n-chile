# Copyright 2026 Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.l10n_cl_dte_sii.services.pdf417 import render_ted_timbre


class AccountMove(models.Model):
    _name = "account.move"
    _inherit = "account.move"

    l10n_cl_dte_pos_ted_image = fields.Char(
        compute="_compute_l10n_cl_dte_pos_ted_image"
    )

    def _check_dte_action_access(self):
        self.ensure_one()
        pos_operator = self.env.user.has_group("point_of_sale.group_pos_user")
        dte_user = self.env.user.has_group("l10n_cl_dte_sii.group_dte_user")
        if pos_operator and not dte_user:
            self.check_access("read")
            if not self.sudo().pos_order_ids:
                raise UserError(
                    _("Sólo se puede operar un DTE vinculado a un pedido POS.")
                )
            if self.company_id not in self.env.companies:
                raise UserError(
                    _("No puede operar documentos de una compañía no permitida.")
                )
            return True
        return super()._check_dte_action_access()

    @api.depends("l10n_cl_dte_attachment_id")
    def _compute_l10n_cl_dte_pos_ted_image(self):
        for move in self:
            move.l10n_cl_dte_pos_ted_image = False
            attachment = move.sudo().l10n_cl_dte_attachment_id
            if not attachment:
                continue
            parser = etree.XMLParser(resolve_entities=False, no_network=True)
            try:
                root = etree.fromstring(attachment.raw, parser=parser)
            except etree.XMLSyntaxError:
                continue
            ted_nodes = root.xpath("//*[local-name()='TED']")
            if not ted_nodes:
                continue
            move.l10n_cl_dte_pos_ted_image = move._l10n_cl_dte_pos_render_ted(
                ted_nodes[0]
            )

    @api.model
    def _l10n_cl_dte_pos_render_ted(self, ted):
        return render_ted_timbre(ted)
