# Copyright 2026 Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Chile - POS DTE Bridge",
    "version": "18.0.3.0.0",
    "category": "Sales/Point of Sale",
    "summary": "Emite documentos tributarios chilenos desde Punto de Venta",
    "author": "Odoo Community Association (OCA)",
    "license": "AGPL-3",
    "depends": ["l10n_cl_dte_sii", "point_of_sale"],
    "external_dependencies": {"python": ["pdf417gen"]},
    "data": [
        "security/security.xml",
        "views/pos_payment_method_views.xml",
        "views/pos_order_views.xml",
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
    "auto_install": True,
    "website": "https://github.com/OCA/l10n-chile",
}
