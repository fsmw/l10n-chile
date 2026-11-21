# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # The shared settings save every dte_inbox_account_ key on the active
    # company under the configuration role.
    dte_inbox_account_received46_journal_id = fields.Many2one(
        related="company_id.dte_inbox_account_received46_journal_id",
        readonly=False,
        domain=(
            "[('company_id', '=', company_id), ('type', '=', 'sale'), "
            "('l10n_latam_use_documents', '=', True)]"
        ),
    )
    dte_inbox_account_received46_vat_tax_id = fields.Many2one(
        related="company_id.dte_inbox_account_received46_vat_tax_id",
        readonly=False,
        domain="[('company_id', '=', company_id), ('type_tax_use', '=', 'sale')]",
    )
    dte_inbox_account_received46_retention_tax_id = fields.Many2one(
        related="company_id.dte_inbox_account_received46_retention_tax_id",
        readonly=False,
        domain="[('company_id', '=', company_id), ('type_tax_use', '=', 'sale')]",
    )
    dte_inbox_account_received46_partial_tax_ids = fields.Many2many(
        related="company_id.dte_inbox_account_received46_partial_tax_ids",
        readonly=False,
        domain="[('company_id', '=', company_id), ('type_tax_use', '=', 'sale')]",
    )
