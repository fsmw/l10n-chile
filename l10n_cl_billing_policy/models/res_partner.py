# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = "res.partner"

    invoicing_policy = fields.Selection(
        [
            ("ticket", "Ticket"),
            ("invoice", "Invoice"),
            ("eguide", "Electronic Guide"),
        ],
        default="invoice",
        required=True,
        help="Default electronic document issued for this partner.\n"
        "* Ticket: only for individuals.\n"
        "* Invoice: only for companies; requires the VAT.\n"
        "* Electronic Guide: only for companies; one invoice at the end "
        "of the month for all the delivery orders of that month.",
    )

    def _root_partner(self):
        self.ensure_one()
        root = self
        while root.parent_id:
            root = root.parent_id
        return root

    def _expected_invoicing_policy(self):
        self.ensure_one()
        return "invoice" if self._root_partner().is_company else "ticket"

    def _check_invoicing_policy(self):
        for partner in self:
            root = partner._root_partner()
            if root.is_company and partner.invoicing_policy == "ticket":
                raise ValidationError(
                    _("The Ticket policy only applies to individuals.")
                )
            if not root.is_company and partner.invoicing_policy != "ticket":
                raise ValidationError(
                    _("The selected policy only applies to companies.")
                )

    @api.model_create_multi
    def create(self, vals_list):
        implicit = [not vals.get("invoicing_policy") for vals in vals_list]
        records = super().create(vals_list)
        for record, defaulted in zip(records, implicit, strict=True):
            if defaulted:
                record.invoicing_policy = record._expected_invoicing_policy()
            else:
                record._check_invoicing_policy()
        return records

    def write(self, values):
        result = super().write(values)
        if {"is_company", "parent_id", "invoicing_policy"} & values.keys():
            self._check_invoicing_policy()
        return result

    @api.onchange("is_company")
    def _onchange_invoicing_policy(self):
        self.invoicing_policy = self._expected_invoicing_policy()
