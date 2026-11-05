# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Chilean Incoming DTE Vendor Bills",
    "summary": "Prepare reviewed incoming DTEs as vendor bill drafts",
    "version": "18.0.1.0.0",
    "development_status": "Alpha",
    "category": "Accounting/Localizations",
    "author": "Fernando San Martin Woerner,Odoo Community Association (OCA)",
    "maintainers": ["fsmw"],
    "license": "AGPL-3",
    "website": "https://github.com/OCA/l10n-chile",
    "depends": ["account", "l10n_cl", "l10n_cl_dte_inbox"],
    "data": [
        "security/incoming_account_security.xml",
        "views/res_config_settings_views.xml",
    ],
    "installable": True,
}
