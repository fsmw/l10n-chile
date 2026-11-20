# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Chile - Registro de Compras y Ventas (RCV) SII",
    "summary": "Períodos, resúmenes y documentos del RCV del SII e importación CSV.",
    "version": "18.0.1.0.0",
    "category": "Accounting/Localizations",
    "author": "FSMW, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "development_status": "Alpha",
    "depends": ["account", "l10n_cl_dte_core"],
    "data": [
        "security/rcv_security.xml",
        "security/ir.model.access.csv",
        "views/rcv_period_views.xml",
        "views/rcv_sync_views.xml",
        "views/rcv_document_views.xml",
        "views/rcv_wizard_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
