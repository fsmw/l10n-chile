# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Chile - DTE Reports",
    "summary": "Render traceable Chilean DTE print samples",
    "version": "18.0.1.0.0",
    "category": "Accounting/Localizations",
    "author": "FSMW, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "development_status": "Alpha",
    "depends": ["l10n_cl_dte_core", "l10n_cl_dte_xml", "l10n_cl_dte_boleta"],
    "external_dependencies": {"python": ["pdf417gen", "pypdf"]},
    "data": [
        "security/report_security.xml",
        "security/ir.model.access.csv",
        "views/report_sample_views.xml",
        "views/electronic_document_views.xml",
        "views/menus.xml",
        "report/dte_document_templates.xml",
        "report/boleta_receipt_templates.xml",
        "report/certification_sample_templates.xml",
    ],
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
