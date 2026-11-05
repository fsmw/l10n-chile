# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
# ruff: noqa: B018
{
    "name": "Chile - DTE Inbox",
    "summary": "Register and review incoming Chilean tax documents",
    "version": "18.0.1.0.0",
    "category": "Accounting/Localizations",
    "author": "FSMW, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "development_status": "Alpha",
    "depends": [
        "l10n_cl_dte_core",
        "l10n_cl_dte_xml",
        "l10n_cl_dte_gateway",
        "l10n_cl_dte_signing",
    ],
    "data": [
        "security/incoming_security.xml",
        "security/ir.model.access.csv",
        "views/incoming_import_wizard_views.xml",
        "views/incoming_document_views.xml",
        "views/incoming_import_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
