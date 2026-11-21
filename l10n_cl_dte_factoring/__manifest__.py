# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Chile - Cesión electrónica de facturas (AEC)",
    "summary": "Assign SII-accepted invoices to a factoring company with an AEC.",
    "version": "18.0.1.0.0",
    "category": "Accounting/Localizations",
    "author": "FSMW, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-chile",
    "license": "AGPL-3",
    "development_status": "Alpha",
    "depends": ["l10n_cl_dte_account", "l10n_cl_dte_gateway"],
    "external_dependencies": {"python": ["cryptography", "requests"]},
    "data": [
        "security/factoring_security.xml",
        "security/ir.model.access.csv",
        "data/queue_job_functions.xml",
        "views/dte_assignment_views.xml",
        "views/account_move_views.xml",
    ],
    "installable": True,
    "application": False,
    "maintainers": ["fsmw"],
}
