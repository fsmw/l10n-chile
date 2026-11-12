# Copyright 2026 FSMW, Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Chile - Impresión de guía de despacho DTE 52",
    "summary": "Datos de impresión de la guía de despacho desde su XML firmado.",
    "version": "18.0.1.0.0",
    "category": "Accounting/Localizations",
    "author": "FSMW, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "development_status": "Alpha",
    "depends": ["l10n_cl_dte_dispatch", "l10n_cl_dte_report"],
    "data": ["report/dispatch_print_templates.xml"],
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
