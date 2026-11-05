# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Chile - POS DTE Returns",
    "summary": "Referenced POS refunds with Chilean credit notes",
    "version": "18.0.1.0.0",
    "category": "Sales/Point of Sale",
    "author": "FSMW,Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "development_status": "Beta",
    "maintainers": ["fsmw"],
    "depends": ["l10n_cl_dte_pos", "l10n_cl_dte_note", "l10n_cl_dte_report"],
    "data": [
        "views/res_company_views.xml",
        "views/res_config_settings_views.xml",
        "views/pos_order_views.xml",
        "views/pos_session_views.xml",
        "views/electronic_document_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": ["l10n_cl_dte_pos_return/static/src/**/*"],
        "web.assets_tests": ["l10n_cl_dte_pos_return/static/tests/tours/**/*"],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
    "images": ["static/description/icon.png"],
}
