# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class DteProductionReadinessWizard(models.TransientModel):
    _name = "l10n_cl.dte.production.readiness.wizard"
    _description = "DTE Production Readiness Wizard"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        check_company=True,
        readonly=True,
    )
    result = fields.Selection(
        [("pending", "Pending"), ("ready", "Ready"), ("blocked", "Blocked")],
        default="pending",
        readonly=True,
    )
    checked_at = fields.Datetime(readonly=True)
    diagnostic_line_ids = fields.One2many(
        "l10n_cl.dte.production.readiness.line", "wizard_id"
    )

    @api.model
    def default_get(self, fields_list):
        """Initialize the company evaluated by this transient wizard."""
        # TODO(oca-implement): enforce allowed-company context [blueprint §4]
        return super().default_get(fields_list)

    def action_check_readiness(self):
        """Evaluate local production prerequisites without external side effects."""
        self.ensure_one()
        # TODO(oca-implement): evaluate and persist local findings
        # [blueprint §4]
        raise NotImplementedError

    def _get_readiness_findings(self):
        """Return ordered local readiness findings for the selected company."""
        # TODO(oca-implement): compose blocker, warning, and success findings
        # [blueprint §4]
        raise NotImplementedError

    def _check_production_signing_profile(self):
        """Check the production signing profile without returning key material."""
        # TODO(oca-implement): validate company, environment, validity, key and RUT
        # [blueprint §4]
        raise NotImplementedError

    def _check_production_cafs(self):
        """Check production CAF coverage and remaining folios for enabled DTE types."""
        # TODO(oca-implement): inspect valid company CAFs and enabled document types
        # [blueprint §4]
        raise NotImplementedError

    def action_open_signing_profiles(self):
        """Open production signing profiles for this company."""
        self.ensure_one()
        # TODO(oca-implement): return a company and production filtered action
        # [blueprint §4]
        raise NotImplementedError

    def action_open_caf_authorizations(self):
        """Open production CAF authorizations for this company."""
        self.ensure_one()
        # TODO(oca-implement): return a company and production filtered action
        # [blueprint §4]
        raise NotImplementedError


class DteProductionReadinessLine(models.TransientModel):
    _name = "l10n_cl.dte.production.readiness.line"
    _description = "DTE Production Readiness Finding"
    _order = "sequence, id"
    _check_company_auto = True

    wizard_id = fields.Many2one(
        "l10n_cl.dte.production.readiness.wizard",
        required=True,
        ondelete="cascade",
        check_company=True,
    )
    sequence = fields.Integer(default=10)
    code = fields.Char(required=True, readonly=True)
    level = fields.Selection(
        [
            ("ok", "Ready"),
            ("warning", "Review"),
            ("blocker", "Blocked"),
        ],
        required=True,
        readonly=True,
    )
    message = fields.Char(required=True, readonly=True)
