# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Chile - DTE Issuance",
    "summary": "Source-agnostic orchestration for Chilean electronic tax documents",
    "version": "18.0.1.0.0",
    "category": "Accounting/Localizations",
    "author": "FSMW, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "images": ["static/description/icon.png"],
    "development_status": "Beta",
    "depends": [
        "l10n_cl_dte_core",
        "l10n_cl_dte_caf",
        "l10n_cl_dte_gateway",
        "l10n_cl_dte_signing",
    ],
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
