# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService

from ..services.production_gateway import SUPPORTED_PRODUCTION_DTE_CODES
from ..services.production_transport import ProductionSiiTransport

_DTE_TYPE_NAMES = {
    "33": "Electronic invoice",
    "52": "Electronic delivery guide",
    "56": "Electronic debit note",
    "61": "Electronic credit note",
}
_DTE_TYPE_SELECTION = [
    (code, f"{code} - {_DTE_TYPE_NAMES[code]}")
    for code in sorted(SUPPORTED_PRODUCTION_DTE_CODES)
]


class DteProductionReadinessWizard(models.TransientModel):
    _name = "l10n_cl.dte.production.readiness.wizard"
    _description = "DTE Production Readiness Wizard"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        readonly=True,
    )
    result = fields.Selection(
        [
            ("pending", "Not checked"),
            ("ready", "Ready (local checks only)"),
            ("blocked", "Blocked"),
        ],
        default="pending",
        readonly=True,
    )
    checked_at = fields.Datetime(readonly=True)
    document_scope_ids = fields.One2many(
        "l10n_cl.dte.production.readiness.scope", "wizard_id"
    )
    diagnostic_line_ids = fields.One2many(
        "l10n_cl.dte.production.readiness.line", "wizard_id"
    )

    @api.model
    def default_get(self, fields_list):
        """Initialize the company evaluated by this transient wizard."""
        values = super().default_get(fields_list)
        company_id = values.get("company_id") or self.env.company.id
        if company_id not in self.env.companies.ids:
            raise AccessError(_("You cannot review production for this company."))
        values["company_id"] = company_id
        return values

    def action_check_readiness(self):
        """Evaluate local production prerequisites without external side effects."""
        self.ensure_one()
        self._check_manager()
        findings = self._get_readiness_findings()
        self.write(
            {
                "checked_at": fields.Datetime.now(),
                "result": (
                    "blocked"
                    if any(item["level"] == "blocker" for item in findings)
                    else "ready"
                ),
                "diagnostic_line_ids": [
                    (5, 0, 0),
                    *[(0, 0, item) for item in findings],
                ],
            }
        )
        return True

    def _get_readiness_findings(self):
        """Return ordered local readiness findings for the selected company."""
        self.ensure_one()
        findings = self._check_company_production_settings()
        findings.extend(self._check_production_signing_profile())
        findings.extend(self._check_production_cafs())
        findings.append(
            self._finding(
                "sii_authentication",
                "warning",
                _(
                    "This local review does not authenticate with or obtain "
                    "approval from SII."
                ),
            )
        )
        return findings

    def _check_company_production_settings(self):
        """Check issuer identity and SII resolution without changing the mode."""
        company = self.company_id
        findings = []
        if not company.dte_sii_resolution_number or not company.dte_sii_resolution_date:
            findings.append(
                self._finding(
                    "sii_resolution",
                    "blocker",
                    _("Set the SII resolution number and date on the company."),
                )
            )
        elif company.dte_sii_resolution_date > fields.Date.context_today(self):
            findings.append(
                self._finding(
                    "sii_resolution_date",
                    "blocker",
                    _("The SII resolution date cannot be in the future."),
                )
            )
        else:
            findings.append(
                self._finding(
                    "sii_resolution",
                    "ok",
                    _("The SII resolution number and date are configured."),
                )
            )
        try:
            ProductionSiiTransport._rut(company.vat)
            ProductionSiiTransport._rut(company.dte_sii_sender_vat)
        except (UserError, ValidationError) as error:
            findings.append(self._finding("sii_sender_rut", "blocker", str(error)))
        else:
            findings.append(
                self._finding(
                    "sii_sender_rut",
                    "ok",
                    _("Company and authorized sender RUTs are configured."),
                )
            )
        return findings

    def _check_production_signing_profile(self):
        """Check the production signing profile without returning key material."""
        self.ensure_one()
        try:
            _profile, holder_rut = SignatureService().signing_identity(
                self.company_id, "production", manager_only=True
            )
            sender_rut = "-".join(
                ProductionSiiTransport._rut(self.company_id.dte_sii_sender_vat)
            )
        except (AccessError, UserError, ValidationError) as error:
            return [self._finding("production_signer", "blocker", str(error))]
        if holder_rut != sender_rut:
            return [
                self._finding(
                    "production_signer_rut",
                    "blocker",
                    _("The signing certificate holder must match the SII sender RUT."),
                )
            ]
        return [
            self._finding(
                "production_signer",
                "ok",
                _("A valid production signing profile and private key are configured."),
            )
        ]

    def _check_production_cafs(self):
        """Check production CAF coverage and remaining folios for enabled DTE types."""
        self.ensure_one()
        codes = sorted(set(self.document_scope_ids.mapped("document_code")))
        if not codes:
            return [
                self._finding(
                    "document_scope",
                    "blocker",
                    _("Select at least one DTE type that the company plans to issue."),
                )
            ]
        findings = []
        today = fields.Date.context_today(self)
        for code in codes:
            cafs = (
                self.env["l10n_cl.caf.authorization"]
                .sudo()
                .search(
                    [
                        ("company_id", "=", self.company_id.id),
                        ("sii_environment", "=", "production"),
                        ("document_code", "=", code),
                        ("state", "=", "validated"),
                        ("is_synthetic", "=", False),
                    ]
                )
            )
            available = False
            available_caf = None
            for caf in cafs:
                if caf.date_from > today or (caf.date_to and caf.date_to < today):
                    continue
                try:
                    caf._check_caf_integrity()
                    caf._next_available_folio()
                except (UserError, ValidationError):
                    continue
                available = True
                available_caf = caf
                break
            level = "ok" if available else "blocker"
            message = (
                _("A valid production CAF has an available folio for DTE %(code)s.")
                % {"code": code}
                if available
                else _(
                    "No valid production CAF with available folios was found for "
                    "DTE %(code)s."
                )
                % {"code": code}
            )
            findings.append(self._finding(f"caf_{code}", level, message))
            if available_caf and available_caf.sii_signature_status != "verified":
                findings.append(
                    self._finding(
                        f"caf_signature_{code}",
                        "warning",
                        _(
                            "The CAF is locally valid, but its SII signature could "
                            "not be verified because the applicable SII trust key "
                            "is unavailable."
                        ),
                    )
                )
        return findings

    @api.model
    def _finding(self, code, level, message):
        return {"code": code, "level": level, "message": message}

    def action_open_signing_profiles(self):
        """Open production signing profiles for this company."""
        self.ensure_one()
        self._check_manager()
        return {
            "type": "ir.actions.act_window",
            "name": _("Production signing profiles"),
            "res_model": "l10n_cl.dte.signing.profile",
            "view_mode": "list,form",
            "domain": [
                ("company_id", "=", self.company_id.id),
                ("sii_environment", "=", "production"),
            ],
            "context": {"default_company_id": self.company_id.id},
        }

    def action_open_caf_authorizations(self):
        """Open production CAF authorizations for this company."""
        self.ensure_one()
        self._check_manager()
        return {
            "type": "ir.actions.act_window",
            "name": _("Production CAF authorizations"),
            "res_model": "l10n_cl.caf.authorization",
            "view_mode": "list,form",
            "domain": [
                ("company_id", "=", self.company_id.id),
                ("sii_environment", "=", "production"),
            ],
            "context": {"default_company_id": self.company_id.id},
        }

    def _check_manager(self):
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(_("Only a DTE manager can review Production readiness."))


class DteProductionReadinessLine(models.TransientModel):
    _name = "l10n_cl.dte.production.readiness.line"
    _description = "DTE Production Readiness Finding"
    _order = "sequence, id"
    _check_company_auto = True

    wizard_id = fields.Many2one(
        "l10n_cl.dte.production.readiness.wizard",
        required=True,
        ondelete="cascade",
        check_company=True,
    )
    company_id = fields.Many2one(
        related="wizard_id.company_id", store=True, readonly=True
    )
    sequence = fields.Integer(default=10)
    code = fields.Char(required=True, readonly=True)
    level = fields.Selection(
        [
            ("ok", "Ready"),
            ("warning", "Review"),
            ("blocker", "Blocked"),
        ],
        required=True,
        readonly=True,
    )
    message = fields.Char(required=True, readonly=True)


class DteProductionReadinessScope(models.TransientModel):
    _name = "l10n_cl.dte.production.readiness.scope"
    _description = "DTE Production Readiness Scope"
    _check_company_auto = True
    _sql_constraints = [
        (
            "scope_code_uniq",
            "unique(wizard_id, document_code)",
            "A DTE type can only be selected once.",
        ),
    ]

    wizard_id = fields.Many2one(
        "l10n_cl.dte.production.readiness.wizard",
        required=True,
        ondelete="cascade",
        check_company=True,
    )
    company_id = fields.Many2one(
        related="wizard_id.company_id", store=True, readonly=True
    )
    document_code = fields.Selection(
        _DTE_TYPE_SELECTION, required=True, string="DTE type"
    )
