# Copyright 2026
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Vincula el código de actividad heredado con el catálogo del SII."""
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    activities = env["l10n_cl.dte.activity"]
    companies = env["res.company"].search([("l10n_cl_dte_activity_code", "!=", False)])
    for company in companies:
        code = (company.l10n_cl_dte_activity_code or "").strip().zfill(6)
        activity = activities.search([("code", "=", code)], limit=1)
        if activity:
            company.l10n_cl_dte_activity_ids = [(6, 0, [activity.id])]
