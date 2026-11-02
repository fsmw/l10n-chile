# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Chile - DTE Credit and Debit Notes",
    "summary": "Issue Chilean electronic credit and debit notes (61 and 56)",
    "version": "18.0.1.0.0",
    "category": "Accounting/Localizations",
    "author": "FSMW, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "development_status": "Alpha",
    "depends": [
        "l10n_cl_dte_account",
        "l10n_cl_dte_core",
        "l10n_cl_dte_gateway",
        "l10n_cl_dte_signing",
        "l10n_cl_dte_xml",
        "account_debit_note",
    ],
    "data": [
        "views/account_move_views.xml",
        "wizards/account_move_reversal_views.xml",
        "wizards/account_debit_note_views.xml",
    ],
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
