# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class CafRequestEvidence(models.Model):
    _name = "l10n_cl.caf.request.evidence"
    _description = "SII CAF Request Evidence"
    _order = "id"
    _check_company_auto = True

    request_id = fields.Many2one(
        "l10n_cl.caf.request", required=True, index=True, ondelete="cascade"
    )
    attempt_id = fields.Many2one(
        "l10n_cl.caf.request.attempt", required=True, index=True, ondelete="cascade"
    )
    company_id = fields.Many2one(
        related="request_id.company_id", store=True, index=True
    )
    environment = fields.Selection(
        [("certification", "Certification"), ("production", "Production")],
        readonly=True,
    )
    endpoint = fields.Char(readonly=True)
    http_status = fields.Integer(readonly=True)
    # The SII response and the CAF embed the CAF private key (RSASK).
    response_file = fields.Binary(
        attachment=True, readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    response_filename = fields.Char(readonly=True)
    response_digest = fields.Char(string="Response SHA-256", readonly=True, index=True)
    response_size = fields.Integer(readonly=True)
    caf_file = fields.Binary(
        attachment=True, readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    caf_filename = fields.Char(readonly=True)
    caf_digest = fields.Char(string="CAF SHA-256", readonly=True, index=True)
    authorization_id = fields.Many2one(
        "l10n_cl.caf.authorization", readonly=True, ondelete="restrict"
    )
    validation_error_code = fields.Char(readonly=True)
    validation_error = fields.Text(readonly=True)
