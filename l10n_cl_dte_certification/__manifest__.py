# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Chile - DTE Certification Sets",
    "summary": "Prepare and export immutable SII certification set evidence",
    "version": "18.0.1.10.0",
    "category": "Accounting/Localizations",
    "author": "FSMW, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "images": ["static/description/icon.png"],
    "license": "AGPL-3",
    "development_status": "Beta",
    "depends": [
        "l10n_cl_dte_core",
        "l10n_cl_dte_account",
        "l10n_cl_dte_note",
        "l10n_cl_dte_gateway",
    ],
    "data": [
        "security/certification_security.xml",
        "security/ir.model.access.csv",
        "views/certification_run_views.xml",
        "views/certification_set_views.xml",
        "views/certification_import_wizard_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
