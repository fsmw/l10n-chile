# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Chile - Respuestas de receptores a DTE emitidos",
    "summary": "Track SII registry events and emailed responses on issued DTEs.",
    "version": "18.0.1.0.0",
    "category": "Accounting/Localizations",
    "author": "FSMW, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "development_status": "Alpha",
    "depends": ["l10n_cl_dte_account", "l10n_cl_dte_inbox"],
    "data": [
        "security/receiver_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/receiver_event_views.xml",
        "views/receiver_response_views.xml",
        "views/electronic_document_views.xml",
        "views/account_move_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
