{
    "name": "Chile - DTE SII Gateway",
    "summary": "Durable submission, polling and evidence for SII DTE exchange",
    "version": "18.0.1.1.0",
    "category": "Accounting/Localizations",
    "author": "Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "depends": ["l10n_cl_dte_core", "l10n_cl_dte_xml", "queue_job"],
    "data": [
        "security/gateway_security.xml",
        "security/ir.model.access.csv",
        "views/electronic_submission_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
