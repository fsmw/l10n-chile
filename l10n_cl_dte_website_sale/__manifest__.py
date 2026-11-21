# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Chile - Comercio electrónico con boleta y factura DTE",
    "summary": "Let eCommerce customers choose a boleta or a factura at checkout.",
    "version": "18.0.1.0.0",
    "category": "Accounting/Localizations",
    "author": "FSMW, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "development_status": "Alpha",
    "depends": ["website_sale", "l10n_cl_dte_account", "l10n_cl_dte_boleta"],
    "data": ["views/sale_order_views.xml", "views/templates.xml"],
    "assets": {
        "web.assets_frontend": [
            "l10n_cl_dte_website_sale/static/src/scss/dte_document.scss",
        ],
    },
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
