# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Folio availability computed from ``l10n_cl_dte_caf``.

The CAF authorizations and the folio reservation ledger of that addon are the
only source of truth; this module keeps no folio ledger of its own.
"""

from odoo import fields


def usable_authorizations(env, company, document_code, environment):
    """Return validated, unexpired CAFs that can still reserve folios."""
    today = fields.Date.context_today(env["l10n_cl.caf.authorization"])
    return (
        env["l10n_cl.caf.authorization"]
        .sudo()
        .search(
            [
                ("company_id", "=", company.id),
                ("document_code", "=", document_code),
                ("sii_environment", "=", environment),
                ("state", "=", "validated"),
                ("date_from", "<=", today),
                "|",
                ("date_to", "=", False),
                ("date_to", ">=", today),
            ]
        )
    )


def available_folios(env, company, document_code, environment):
    """Return the folios that the usable CAFs can still reserve."""
    authorizations = usable_authorizations(env, company, document_code, environment)
    if not authorizations:
        return 0
    used = dict(
        env["l10n_cl.caf.folio"]
        .sudo()
        ._read_group(
            [("authorization_id", "in", authorizations.ids)],
            groupby=["authorization_id"],
            aggregates=["__count"],
        )
    )
    return sum(
        max(
            authorization.folio_end
            - authorization.folio_start
            + 1
            - used.get(authorization, 0),
            0,
        )
        for authorization in authorizations
    )
