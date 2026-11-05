# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Chile - POS DTE Boletas",
    "summary": "Issue Chilean DTE boletas from Point of Sale",
    "version": "18.0.1.0.0",
    "category": "Sales/Point of Sale",
    "author": "FSMW,Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "development_status": "Beta",
    "images": ["static/description/icon.png"],
    "depends": [
        "point_of_sale",
        "l10n_cl_dte_account",
        "l10n_cl_dte_boleta",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/res_company_views.xml",
        "views/pos_order_views.xml",
        "views/pos_session_views.xml",
        "views/pos_payment_method_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "l10n_cl_dte_pos/static/src/**/*",
        ],
        "web.assets_tests": [
            "l10n_cl_dte_pos/static/tests/tours/**/*",
        ],
    },
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
