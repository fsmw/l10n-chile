# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Chile - SII DTE Emission",
    "version": "18.0.7.0.0",
    "category": "Accounting/Localizations/EDI",
    "summary": "Emite y consulta DTE de salida ante el SII",
    "author": (
        "Odoo Community Association (OCA), "
        "Fernando San Martin Woerner <fsanmartin@iternova.cl>"
    ),
    "license": "AGPL-3",
    "depends": ["l10n_cl", "account_edi", "account_debit_note", "certificate"],
    "external_dependencies": {"python": ["pdf417gen"]},
    "data": [
        "security/security.xml",
        "security/dte_reconciliation_security.xml",
        "security/ir.model.access.csv",
        "data/mail_template_data.xml",
        "data/l10n_cl.dte.activity.csv",
        "views/account_move_views.xml",
        "views/account_move_search_views.xml",
        "views/report_invoice.xml",
        "views/res_company_views.xml",
        "views/account_journal_views.xml",
        "views/certificate_views.xml",
        "views/dte_caf_views.xml",
        "views/dte_company_settings_views.xml",
        "views/dte_menus.xml",
        "wizard/account_move_reversal_views.xml",
        "wizard/account_move_debit_note_views.xml",
        "wizard/dte_reconciliation_views.xml",
    ],
    "assets": {
        "web.assets_tests": [
            "l10n_cl_dte_sii/static/tests/tours/**/*",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
    "auto_install": False,
    "website": "https://github.com/OCA/l10n-chile",
}
