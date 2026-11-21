# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Chile - Reportes tributarios F29 y balance de 8 columnas",
    "summary": "F29 monthly VAT worksheet and the Chilean eight-column balance.",
    "version": "18.0.1.0.0",
    "category": "Accounting/Localizations",
    "author": "FSMW, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "development_status": "Alpha",
    "depends": ["account", "l10n_cl"],
    "data": [
        "security/ir.model.access.csv",
        "security/tax_reports_security.xml",
        "data/f29_box_data.xml",
        "report/eight_column_balance_report.xml",
        "report/f29_report.xml",
        "views/eight_column_balance_views.xml",
        "views/f29_report_views.xml",
        "views/f29_box_views.xml",
        "views/res_config_settings_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
