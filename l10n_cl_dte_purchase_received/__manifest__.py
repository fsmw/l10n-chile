# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Chile - Recepción de facturas de compra DTE 46",
    "summary": "Registra como venta la factura de compra DTE 46 recibida del comprador",
    "version": "18.0.1.0.0",
    "development_status": "Alpha",
    "category": "Accounting/Localizations",
    "author": "Fernando San Martin Woerner,Odoo Community Association (OCA)",
    "maintainers": ["fsmw"],
    "license": "AGPL-3",
    "website": "https://github.com/OCA/l10n-chile",
    "depends": ["l10n_cl_dte_inbox_account", "l10n_cl_dte_account"],
    "data": [
        "views/res_config_settings_views.xml",
        "wizards/incoming_bill_wizard_views.xml",
        "views/incoming_document_views.xml",
    ],
    "installable": True,
}
