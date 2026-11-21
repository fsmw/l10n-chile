# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

SALE_MOVE_TYPES = ("out_invoice", "out_refund")
PURCHASE_MOVE_TYPES = ("in_invoice", "in_refund")


class L10nClF29Box(models.Model):
    """One configurable F29 line computed from posted journal entries.

    A box selects posted invoices of a scope (sales or purchases), optionally
    restricted to some document types, and measures either the number of
    documents, their untaxed amount or the tax-report balance of the tax tags
    it lists (on tax lines or on base lines).
    """

    _name = "l10n_cl.f29.box"
    _description = "F29 Worksheet Box"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        help="Leave empty to use this box in every company.",
    )
    section = fields.Selection(
        [
            ("sale", "VAT Debits (Sales)"),
            ("purchase", "VAT Credits (Purchases)"),
            ("withholding", "Withholdings"),
            ("info", "Informative"),
        ],
        required=True,
        default="sale",
    )
    count_code = fields.Char(
        string="Count Box", help="SII F29 box holding the number of documents."
    )
    amount_code = fields.Char(
        string="Amount Box", help="SII F29 box holding the amount."
    )
    move_scope = fields.Selection(
        [("sale", "Customer Documents"), ("purchase", "Vendor Documents")],
        required=True,
        default="sale",
    )
    document_type_ids = fields.Many2many(
        "l10n_latam.document.type",
        string="Document Types",
        help="Leave empty to use every document type.",
    )
    amount_source = fields.Selection(
        [
            ("tax", "Tax Lines with Tags"),
            ("base", "Base Lines with Tags"),
            ("untaxed", "Untaxed Amount of the Documents"),
            ("none", "No Amount (Count Only)"),
        ],
        required=True,
        default="tax",
    )
    tag_names = fields.Text(
        help="Tax report tag names, one per line, without the +/- sign "
        "(e.g. 'IVA Debito Fiscal'). Both signed tags are used.",
    )
    tag_ids = fields.Many2many(
        "account.account.tag",
        string="Additional Tags",
        domain=[("applicability", "=", "taxes")],
    )
    sign = fields.Selection(
        [("1", "As Reported"), ("-1", "Inverted")],
        required=True,
        default="1",
        help="Use 'Inverted' to show negative balances (credit notes, "
        "withholdings) as positive amounts on the form.",
    )
    total_effect = fields.Selection(
        [
            ("debit", "Adds to Total Debits"),
            ("debit_reduce", "Reduces Total Debits"),
            ("credit", "Adds to Total Credits"),
            ("credit_reduce", "Reduces Total Credits"),
            ("withholding", "Adds to Withholdings"),
            ("none", "None"),
        ],
        required=True,
        default="none",
    )
    apply_proportional_factor = fields.Boolean(
        help="Multiply the amount by the company proportional factor "
        "(common-use VAT credit).",
    )

    @api.model
    def _get_active_boxes(self, company):
        return self.search(
            [("company_id", "in", [False, company.id])], order="sequence, id"
        )

    def _get_tags(self):
        self.ensure_one()
        tags = self.tag_ids
        country = self.env.ref("base.cl")
        tag_model = self.env["account.account.tag"].with_context(
            active_test=False, lang="en_US"
        )
        for tag_name in (self.tag_names or "").splitlines():
            tag_name = tag_name.strip()
            if tag_name:
                domain = tag_model._get_tax_tags_domain(tag_name, country.id)
                tags |= tag_model.search(domain)
        return tags

    def _get_move_domain(self, company, date_from, date_to):
        self.ensure_one()
        move_types = (
            SALE_MOVE_TYPES if self.move_scope == "sale" else PURCHASE_MOVE_TYPES
        )
        domain = [
            ("company_id", "=", company.id),
            ("state", "=", "posted"),
            ("date", ">=", date_from),
            ("date", "<=", date_to),
            ("move_type", "in", move_types),
        ]
        if self.document_type_ids:
            domain.append(
                ("l10n_latam_document_type_id", "in", self.document_type_ids.ids)
            )
        return domain

    def _compute_values(self, company, date_from, date_to, factor):
        """Return ``(document count, amount)`` of the box for a period."""
        self.ensure_one()
        move_domain = self._get_move_domain(company, date_from, date_to)
        if self.amount_source in ("untaxed", "none"):
            moves = self.env["account.move"].search(move_domain)
            direction = 1 if self.move_scope == "sale" else -1
            amount = direction * sum(moves.mapped("amount_untaxed_signed"))
            if self.amount_source == "none":
                amount = 0.0
        else:
            moves, amount = self._compute_tag_values(move_domain)
        amount *= int(self.sign)
        if self.apply_proportional_factor:
            amount = amount * factor / 100.0
        return len(moves), company.currency_id.round(amount)

    def _compute_tag_values(self, move_domain):
        tags = self._get_tags()
        if not tags:
            return self.env["account.move"], 0.0
        operator = "!=" if self.amount_source == "tax" else "="
        line_domain = [
            ("move_id", "any", move_domain),
            ("tax_tag_ids", "in", tags.ids),
            ("tax_repartition_line_id", operator, False),
        ]
        lines = self.env["account.move.line"].search(line_domain)
        amount = 0.0
        for line in lines:
            line_sign = -1 if line.tax_tag_invert else 1
            for tag in line.tax_tag_ids & tags:
                tag_sign = -1 if tag.tax_negate else 1
                amount += line.balance * line_sign * tag_sign
        return lines.move_id, amount
