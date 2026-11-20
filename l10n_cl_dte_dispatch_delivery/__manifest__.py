# Copyright 2026 FSMW, Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Chile - Envío de guías de despacho DTE 52 al cliente",
    "summary": "Envía por correo la guía aceptada por el SII con su XML y PDF.",
    "version": "18.0.1.0.0",
    "category": "Accounting/Localizations",
    "author": "FSMW, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "development_status": "Alpha",
    "depends": ["l10n_cl_dte_dispatch_report", "l10n_cl_dte_delivery"],
    "data": ["data/mail_template_data.xml", "views/dispatch_views.xml"],
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
