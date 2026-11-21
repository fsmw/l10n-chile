# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Chile - Liquidación-factura electrónica DTE 43",
    "summary": "Emite la liquidación-factura DTE 43 del comisionista a su mandante.",
    "version": "18.0.1.0.0",
    "category": "Accounting/Localizations",
    "author": "FSMW, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "development_status": "Alpha",
    "depends": ["l10n_cl_dte_account"],
    "data": [
        "data/l10n_latam_document_type_data.xml",
        "views/account_move_views.xml",
    ],
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
