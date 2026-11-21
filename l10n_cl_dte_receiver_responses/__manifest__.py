# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Chile - Respuestas de intercambio a DTE recibidos",
    "summary": "Sign and email RespuestaDTE and EnvioRecibos for supplier DTEs.",
    "version": "18.0.1.0.0",
    "category": "Accounting/Localizations",
    "author": "FSMW, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "development_status": "Alpha",
    "depends": ["mail", "l10n_cl_dte_inbox"],
    "external_dependencies": {"python": ["cryptography"]},
    "data": [
        "security/exchange_security.xml",
        "security/ir.model.access.csv",
        "data/mail_template_data.xml",
        "views/incoming_exchange_views.xml",
        "views/incoming_document_views.xml",
        "views/res_company_views.xml",
        "wizards/exchange_wizard_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
