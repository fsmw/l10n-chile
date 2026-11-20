# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Chile - DTE Export Documents",
    "summary": "Customs catalog and source inputs for export invoices and notes",
    "version": "18.0.1.6.0",
    "category": "Accounting/Localizations",
    "author": "FSMW, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "development_status": "Alpha",
    "depends": ["l10n_cl_dte_note", "l10n_cl_dte_issuance"],
    "data": [
        "security/ir.model.access.csv",
        "data/export_catalog_data.xml",
        "views/export_catalog_views.xml",
        "views/export_adjustment_wizard_views.xml",
        "views/account_move_views.xml",
    ],
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
