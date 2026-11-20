# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Chile - DTE Dispatch Guides from Stock",
    "summary": "Create DTE 52 dispatch guides from validated deliveries",
    "version": "18.0.1.0.0",
    "category": "Inventory/Inventory",
    "author": "FSMW, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "development_status": "Alpha",
    "depends": [
        "stock",
        "l10n_cl_dte_dispatch",
    ],
    "data": [
        "views/stock_picking_views.xml",
        "views/res_company_views.xml",
    ],
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
