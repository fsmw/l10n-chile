# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Chile - RCV con bandeja de DTE recibidos",
    "summary": "Link the RCV with received DTEs and prepare missing vendor bills.",
    "version": "18.0.1.0.0",
    "category": "Accounting/Localizations",
    "author": "FSMW, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "development_status": "Alpha",
    "depends": ["l10n_cl_dte_rcv", "l10n_cl_dte_inbox_account"],
    "data": [
        "security/ir.model.access.csv",
        "data/rcv_inbox_cron.xml",
        "wizard/rcv_draft_wizard_views.xml",
        "views/rcv_document_views.xml",
        "views/incoming_dte_views.xml",
        "views/res_company_views.xml",
    ],
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
