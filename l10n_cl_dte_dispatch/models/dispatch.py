# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models

DISPATCH_SEQUENCE_CODE = "l10n_cl.dte.dispatch"


class DteDispatch(models.Model):
    _name = "l10n_cl.dte.dispatch"
    _description = "Electronic dispatch guide (DTE 52)"

    name = fields.Char(
        required=True,
        copy=False,
        index=True,
        default=lambda self: self.env["ir.sequence"].next_by_code(
            DISPATCH_SEQUENCE_CODE
        ),
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        index=True,
        default=lambda self: self.env.company,
    )
    sii_environment = fields.Selection(
        [("certification", "Certification"), ("production", "Production")],
        required=True,
    )
    source_kind = fields.Selection(
        [("manual", "Manual"), ("certification", "Certification")],
        required=True,
    )
    source_key = fields.Char(required=True, index=True, copy=False)
    issue_date = fields.Date(required=True)
    partner_id = fields.Many2one("res.partner", required=True)
    transfer_indicator = fields.Selection(
        [(str(number), str(number)) for number in range(1, 10)],
        required=True,
    )
    dispatch_type_code = fields.Char(required=True)
    origin_address = fields.Char(required=True)
    destination_address = fields.Char(required=True)
    carrier_id = fields.Many2one("res.partner")
    driver_id = fields.Many2one("res.partner")
    vehicle_plate = fields.Char()
    departure_at = fields.Datetime()
    arrival_at = fields.Datetime()
    currency_id = fields.Many2one("res.currency", required=True)
    line_ids = fields.One2many("l10n_cl.dte.dispatch.line", "dispatch_id")
    reference_ids = fields.One2many("l10n_cl.dte.dispatch.reference", "dispatch_id")
    snapshot_schema = fields.Char(readonly=True, copy=False)
    snapshot_json = fields.Text(readonly=True, copy=False)
    snapshot_digest = fields.Char(readonly=True, copy=False, index=True)
    state = fields.Selection(
        [("draft", "Draft"), ("reviewed", "Reviewed"), ("frozen", "Frozen")],
        default="draft",
        required=True,
        index=True,
    )

    _sql_constraints = [
        (
            "source_key_company_uniq",
            "unique(company_id, source_key)",
            "A source can have only one dispatch guide per company.",
        ),
    ]
