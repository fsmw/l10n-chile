# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Chile - DTE Customer Delivery",
    "summary": "Email accepted DTEs to customers with their XML and stamped PDF",
    "version": "18.0.1.0.0",
    "category": "Accounting/Localizations",
    "author": "FSMW, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "development_status": "Beta",
    "depends": [
        "mail",
        "l10n_cl_dte_gateway",
        "l10n_cl_dte_report",
    ],
    "data": [
        "data/mail_template_data.xml",
        "views/account_move_views.xml",
        "views/res_partner_views.xml",
    ],
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
