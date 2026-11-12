# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.l10n_cl_dte_issuance.services.dte_issuance_service import (
    DteIssuanceService,
)

from ..services.dispatch_issuance_adapter import DispatchIssuanceAdapter

DISPATCH_SEQUENCE_CODE = "l10n_cl.dte.dispatch"
DOCUMENT_CODE = "52"
SNAPSHOT_MODEL = "l10n_cl.dte.dispatch.snapshot"
VALIDATOR_MODEL = "l10n_cl.dte.dispatch.validator"
# Fields that define the fiscal content. Changing any of them after the
# snapshot is frozen would make the stored evidence stale.
FROZEN_FIELDS = frozenset(
    {
        "name",
        "company_id",
        "sii_environment",
        "source_kind",
        "source_key",
        "issue_date",
        "partner_id",
        "transfer_indicator",
        "dispatch_type_code",
        "origin_address",
        "destination_address",
        "currency_id",
        "carrier_id",
        "driver_id",
        "vehicle_plate",
        "departure_at",
        "arrival_at",
        "state",
    }
)


class DteDispatch(models.Model):
    _name = "l10n_cl.dte.dispatch"
    _description = "Electronic dispatch guide (DTE 52)"

    name = fields.Char(
        required=True,
        copy=False,
        index=True,
        default=lambda self: (
            self.env["ir.sequence"].sudo().next_by_code(DISPATCH_SEQUENCE_CODE)
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
    document_ids = fields.One2many(
        "l10n_cl.electronic.document",
        "source_dispatch_id",
        string="Electronic Documents",
    )
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

    def write(self, vals):
        if FROZEN_FIELDS.intersection(vals):
            self._check_not_frozen()
        return super().write(vals)

    def unlink(self):
        self._check_not_frozen()
        return super().unlink()

    def _check_not_frozen(self):
        if self.filtered(lambda dispatch: dispatch.state == "frozen"):
            raise UserError(_("A frozen dispatch cannot be changed."))

    def action_review(self):
        """Validate the local rules and move the dispatch to reviewed."""
        validator = self.env[VALIDATOR_MODEL]
        for dispatch in self:
            errors = validator.validate(dispatch)
            if errors:
                raise UserError(
                    _("The dispatch cannot be reviewed:\n%s", "\n".join(errors))
                )
            dispatch.write({"state": "reviewed"})

    def action_prepare_document(self):
        """Sign the frozen guide as DTE 52 and keep its evidence.

        Nothing is sent to SII here.
        """
        self.ensure_one()
        adapter = DispatchIssuanceAdapter(self.env["l10n_cl.dte.dispatch.xml.composer"])
        return DteIssuanceService().prepare(self, adapter, DOCUMENT_CODE)

    def action_freeze_snapshot(self):
        """Store the canonical snapshot and its digest, then freeze the dispatch."""
        snapshot = self.env[SNAPSHOT_MODEL]
        for dispatch in self:
            if dispatch.state != "reviewed":
                raise UserError(_("Review the dispatch before freezing its snapshot."))
            serialized = snapshot.serialize(snapshot.capture(dispatch))
            dispatch.write(
                {
                    "snapshot_schema": "dte52-gd-snapshot-v1",
                    "snapshot_json": serialized,
                    "snapshot_digest": snapshot.digest(serialized),
                    "state": "frozen",
                }
            )
