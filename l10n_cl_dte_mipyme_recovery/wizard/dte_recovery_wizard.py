# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Operator wizard for explicit MiPyme recovery requests."""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services.identity import (
    DTE_TYPES,
    normalize_dte_type,
    normalize_folio,
    normalize_rut,
)
from ..services.mipyme_adapter import ProductionMipymeAdapter


class DteRecoveryWizard(models.TransientModel):
    _name = "l10n_cl.dte.recovery.wizard"
    _description = "Recover Issued DTE from SII MiPyme"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        readonly=True,
        default=lambda self: self.env.company,
    )
    issuer_rut = fields.Char(compute="_compute_issuer_rut", readonly=True)
    dte_type = fields.Selection(selection="_dte_type_selection", required=True)
    folio = fields.Integer(required=True)
    date_from = fields.Date(
        string="From", required=True, default=fields.Date.context_today
    )
    date_to = fields.Date(string="To", required=True, default=fields.Date.context_today)
    result_status = fields.Selection(related="recovery_id.status", readonly=True)
    result_message = fields.Text(readonly=True)
    result_digest = fields.Char(
        string="SHA-256", related="recovery_id.digest", readonly=True
    )
    recovery_id = fields.Many2one("l10n_cl.dte.recovery", readonly=True)
    evidence_url = fields.Char(related="recovery_id.evidence_url", readonly=True)
    move_id = fields.Many2one(
        "account.move", related="recovery_id.move_id", readonly=True
    )
    recipient_foreign_id = fields.Char(
        string="Identificador extranjero",
        compute="_compute_recipient_identity",
        readonly=True,
    )
    recipient_country_code = fields.Char(
        string="País declarado en el XML",
        compute="_compute_recipient_identity",
        readonly=True,
    )
    resolved_country_id = fields.Many2one(
        "res.country",
        string="País del cliente",
        compute="_compute_recipient_identity",
        readonly=True,
    )
    recipient_name = fields.Char(
        string="Nombre del receptor en XML",
        compute="_compute_recipient_identity",
        readonly=True,
    )
    candidate_partner_ids = fields.Many2many(
        "res.partner",
        string="Clientes compatibles",
        compute="_compute_recipient_identity",
        readonly=True,
    )
    needs_recipient_confirmation = fields.Boolean(
        string="Requiere confirmación",
        compute="_compute_recipient_identity",
        readonly=True,
    )
    selected_partner_id = fields.Many2one(
        "res.partner",
        domain="[('id', 'in', candidate_partner_ids)]",
        string="Cliente compatible",
    )
    resolution_reason = fields.Text(string="Motivo de confirmación")

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        recovery_id = self.env.context.get("default_recovery_id")
        if recovery_id:
            recovery = self.env["l10n_cl.dte.recovery"].browse(recovery_id).exists()
            if recovery:
                recovery.check_access("read")
                values.update(
                    {
                        "recovery_id": recovery.id,
                        "company_id": recovery.company_id.id,
                        "dte_type": recovery.dte_type,
                        "folio": recovery.folio,
                        "date_from": recovery.date_from,
                        "date_to": recovery.date_to,
                        "result_message": recovery.diagnostic,
                    }
                )
        return values

    @api.depends(
        "recovery_id", "recovery_id.digest", "recovery_id.recipient_partner_id"
    )
    def _compute_recipient_identity(self):
        for wizard in self:
            wizard.recipient_foreign_id = False
            wizard.recipient_country_code = False
            wizard.resolved_country_id = False
            wizard.recipient_name = False
            wizard.candidate_partner_ids = False
            wizard.needs_recipient_confirmation = False
            recovery = wizard.recovery_id
            if not recovery or not recovery.attachment_id:
                continue
            try:
                _candidate, data = recovery._accounting_data_from_attachment()
                if recovery.recipient_partner_id and (
                    recovery._stored_recipient_resolution_is_current(
                        data, recovery.company_id
                    )
                ):
                    resolution = {
                        "state": "resolved",
                        "country": recovery.recipient_country_id,
                        "partner": recovery.recipient_partner_id,
                        "candidates": recovery.recipient_partner_id,
                    }
                else:
                    resolution = recovery._resolve_export_recipient(
                        data, recovery.company_id
                    )
            except (UserError, ValueError):
                continue
            wizard.recipient_foreign_id = data.recipient_foreign_id or False
            wizard.recipient_country_code = data.recipient_country_code or False
            wizard.resolved_country_id = resolution["country"]
            wizard.recipient_name = data.recipient_name or False
            wizard.candidate_partner_ids = resolution["candidates"]
            wizard.needs_recipient_confirmation = (
                resolution["state"] == "confirmation_required"
            )

    @api.model
    def _dte_type_selection(self):
        document_types = self.env["l10n_latam.document.type"].search(
            [
                ("code", "in", list(DTE_TYPES)),
                ("country_id.code", "=", "CL"),
            ],
            order="code",
        )
        names = {str(document.code): document.name for document in document_types}
        return [
            (code, f"({code}) {names.get(code, 'DTE electrónico')}")
            for code in sorted(DTE_TYPES, key=int)
        ]

    @api.depends("company_id", "company_id.vat")
    def _compute_issuer_rut(self):
        for wizard in self:
            value = wizard.company_id.vat or ""
            try:
                wizard.issuer_rut = normalize_rut(value)
            except ValueError:
                wizard.issuer_rut = value or False

    def _validate_input(self):
        self.ensure_one()
        if not self.env.user.has_group(
            "l10n_cl_dte_mipyme_recovery.group_mipyme_recovery_user"
        ):
            raise AccessError(_("You are not authorized to recover MiPyme DTEs."))
        try:
            normalize_rut(self.issuer_rut)
            normalize_dte_type(self.dte_type)
            normalize_folio(self.folio)
        except ValueError as error:
            raise ValidationError(str(error)) from error
        if not self.date_from or not self.date_to:
            raise ValidationError(_("Both recovery dates are required."))
        if self.date_from > self.date_to:
            raise ValidationError(
                _("The start date cannot be later than the end date.")
            )
        if self.company_id.country_id.code != "CL":
            raise ValidationError(_("MiPyme recovery requires a Chilean company."))

    def _get_adapter(self):
        return ProductionMipymeAdapter(self.env)

    def action_recover(self):
        self.ensure_one()
        self._validate_input()
        recovery = self.env["l10n_cl.dte.recovery"].recover_issued_document(
            self.company_id,
            self.issuer_rut,
            self.dte_type,
            self.folio,
            self.date_from,
            self.date_to,
            adapter=self._get_adapter(),
        )
        self.write(
            {
                "recovery_id": recovery.id,
                "result_message": recovery.diagnostic or _("Recovery completed."),
            }
        )
        if recovery.move_id:
            return self._open_move_action(recovery.move_id)
        return {
            "type": "ir.actions.act_window",
            "name": _("Recover Issued DTE from SII MiPyme"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_import_to_odoo(self):
        self.ensure_one()
        if not self.recovery_id:
            raise ValidationError(_("Recover the DTE before importing it."))
        move = self.recovery_id.action_import_to_odoo()
        if isinstance(move, dict):
            return move
        self.move_id = move.id
        return self._open_move_action(move)

    def action_confirm_recipient(self):
        self.ensure_one()
        if not self.recovery_id or not self.selected_partner_id:
            raise ValidationError(_("Seleccione un cliente compatible con el XML."))
        self.recovery_id.action_confirm_recipient(
            self.selected_partner_id.id, self.resolution_reason
        )
        self.result_message = _(
            "Cliente confirmado. Importe para ejecutar las validaciones fiscales."
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Resolver receptor de exportación"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _open_move_action(self, move):
        return {
            "type": "ir.actions.act_window",
            "name": _("Imported DTE"),
            "res_model": "account.move",
            "res_id": move.id,
            "view_mode": "form",
            "target": "current",
        }
