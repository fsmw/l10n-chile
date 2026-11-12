# Copyright 2026 FSMW, Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Chile - Documento GD electrónico DTE 52 (modelo)",
    "summary": "Guía de despacho electrónica: modelo, líneas, referencias y eventos.",
    "version": "18.0.1.0.0",
    "category": "Accounting/Localizations",
    "author": "FSMW, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "development_status": "Alpha",
    "depends": [
        "l10n_cl_dte_core",
        "l10n_cl_dte_caf",
        "l10n_cl_dte_signing",
        "l10n_cl_dte_xml",
        "product",
    ],
    "data": [
        "data/dispatch_sequence.xml",
        "security/dispatch_security.xml",
        "security/ir.model.access.csv",
        "views/dispatch_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
