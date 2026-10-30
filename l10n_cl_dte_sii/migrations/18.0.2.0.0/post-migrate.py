# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import SUPERUSER_ID, api

from odoo.addons.l10n_cl_dte_sii.hooks import migrate_legacy_account_moves


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    migrate_legacy_account_moves(env)
