# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _
from odoo.exceptions import AccessError, UserError


def check_certification_access(env, company):
    """Recheck the operational boundary on every server-side action."""
    if not env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
        raise AccessError(_("A DTE manager is required for certification operations."))
    if company.id != env.company.id or company not in env.companies:
        raise AccessError(_("Select the certification company as the active company."))
    if company.dte_sii_environment != "certification":
        raise UserError(_("The SII environment must be Certification."))
