# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Chile - Solicitud automática de CAF al SII",
    "summary": "Solicita CAF al SII con evidencia auditable y reglas de reposición.",
    "version": "18.0.1.0.0",
    "category": "Accounting/Localizations",
    "author": "FSMW, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "development_status": "Alpha",
    "depends": [
        "mail",
        "l10n_cl_dte_core",
        "l10n_cl_dte_caf",
        "l10n_cl_dte_signing",
    ],
    "external_dependencies": {"python": ["cryptography", "pycurl"]},
    "data": [
        "security/caf_request_security.xml",
        "security/ir.model.access.csv",
        "data/caf_request_sequence.xml",
        "data/caf_request_cron.xml",
        "views/caf_request_views.xml",
        "views/caf_replenishment_rule_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
