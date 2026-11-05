# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json

from odoo import api, fields, models
from odoo.exceptions import AccessError

from ..services.common import reject
from ..services.mapping_service import IncomingAccountMappingService


class IncomingDteAccountMapping(models.Model):
    _name = "l10n_cl.incoming.dte.account.mapping"
    _description = "Incoming DTE Purchase Mapping"
    _check_company_auto = True
    _order = "company_id, partner_id, match_kind, id"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        ondelete="restrict",
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Commercial Supplier",
        required=True,
        check_company=True,
        index=True,
        ondelete="restrict",
    )
    match_kind = fields.Selection(
        [
            ("code", "Item Code"),
            ("description", "Exact Description"),
            ("default", "Supplier Default"),
        ],
        required=True,
        default="code",
    )
    source_code_type = fields.Char(string="XML Code Type")
    source_value = fields.Char(string="XML Code or Description")
    match_key = fields.Char(
        compute="_compute_match_key",
        store=True,
        precompute=True,
        required=True,
        readonly=True,
    )
    product_id = fields.Many2one(
        "product.product", check_company=True, ondelete="restrict"
    )
    account_id = fields.Many2one(
        "account.account",
        required=True,
        check_company=True,
        ondelete="restrict",
        domain=(
            "[('company_ids', 'in', [company_id]), ('deprecated', '=', "
            "False), ('account_type', 'not in', ['asset_receivable', "
            "'liability_payable', 'off_balance'])]"
        ),
    )
    tax_id = fields.Many2one(
        "account.tax",
        required=True,
        check_company=True,
        ondelete="restrict",
        domain=("[('company_id', '=', company_id), ('type_tax_use', '=', 'purchase')]"),
    )
    uom_id = fields.Many2one("uom.uom", string="Invoice Unit", ondelete="restrict")
    source_uom = fields.Char(string="XML Unit")

    _sql_constraints = [
        (
            "supplier_mapping_key_uniq",
            "unique(company_id, partner_id, match_key)",
            ("This supplier mapping key already exists, including archived mappings."),
        ),
    ]

    @api.depends("match_kind", "source_code_type", "source_value")
    def _compute_match_key(self):
        for record in self:
            parts = [record.match_kind]
            if record.match_kind == "code":
                parts.extend([record.source_code_type or "", record.source_value or ""])
            elif record.match_kind == "description":
                parts.append(" ".join((record.source_value or "").split()))
            record.match_key = json.dumps(
                parts, ensure_ascii=False, separators=(",", ":")
            )

    @api.constrains(
        "company_id",
        "partner_id",
        "match_kind",
        "source_code_type",
        "source_value",
        "product_id",
        "account_id",
        "tax_id",
        "uom_id",
        "source_uom",
    )
    def _check_mapping(self):
        for record in self:
            if (
                record.partner_id != record.partner_id.commercial_partner_id
                or record.partner_id.company_id
                not in (record.company_id, self.env["res.company"])
            ):
                reject(self.env, "supplier_ambiguous")
            if (record.match_kind == "code" and not record.source_code_type) or (
                record.match_kind != "default"
                and not (record.source_value or "").strip()
            ):
                reject(self.env, "mapping_missing")
            source = dict(
                ordinal=0,
                exempt=record.tax_id.amount == 0,
                unit=record.source_uom or "",
            )
            values = dict(
                account_id=record.account_id.id,
                tax_id=record.tax_id.id,
                product_id=record.product_id.id,
                uom_id=record.uom_id.id,
            )
            IncomingAccountMappingService(self.env)._validate_line(
                source, values, record.company_id
            )

    @api.model_create_multi
    def create(self, vals_list):
        if any("match_key" in values for values in vals_list):
            raise AccessError(self.env._("The purchase mapping key is server-owned."))
        return super().create(vals_list)

    def write(self, vals):
        if "match_key" in vals:
            raise AccessError(self.env._("The purchase mapping key is server-owned."))
        return super().write(vals)
