# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Compatibility import: the service now lives in ``l10n_cl_dte_issuance``."""

from odoo.addons.l10n_cl_dte_issuance.services.dte_issuance_service import (  # noqa: F401
    DteIssuanceService,
)
