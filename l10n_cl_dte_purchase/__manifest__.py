# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Chile - Factura de compra electrónica DTE 46",
    "summary": "Emite la factura de compra DTE 46 desde facturas de proveedor.",
    "version": "18.0.1.1.0",
    "category": "Accounting/Localizations",
    "author": "FSMW, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "development_status": "Alpha",
    "depends": ["l10n_cl_dte_account"],
    "data": ["views/account_move_views.xml"],
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
