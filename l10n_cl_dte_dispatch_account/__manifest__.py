# Copyright 2026 FSMW, Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Chile - Guías de despacho DTE 52 en facturas",
    "summary": "Referencia una guía de despacho emitida desde la factura.",
    "version": "18.0.1.0.0",
    "category": "Accounting/Localizations",
    "author": "FSMW, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "development_status": "Alpha",
    "depends": ["l10n_cl_dte_dispatch", "l10n_cl_dte_account"],
    "data": ["views/account_move_views.xml"],
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
