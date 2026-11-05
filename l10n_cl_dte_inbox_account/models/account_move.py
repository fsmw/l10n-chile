# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services.binding_context import native_context, native_guard, trusted
from ..services.binding_service import IncomingAccountBindingService
from ..services.common import (
    BINDING,
    GROUP,
    canonical_folio,
    reject,
    source_snapshot,
    vat,
)
from ..services.comparison_service import IncomingAccountComparisonService
from ..services.evidence_service import IncomingAccountEvidenceService


class AccountMove(models.Model):
    _inherit = "account.move"

    dte_inbox_binding_ids = fields.One2many(
        "l10n_cl.incoming.dte.account.binding",
        "move_id",
        string="Incoming DTE Links",
        readonly=True,
        copy=False,
        groups="l10n_cl_dte_inbox_account.group_inbox_account_user",
    )

    @api.model_create_multi
    def create(self, vals_list):
        if not trusted(self.env):
            for vals in vals_list:
                if "dte_inbox_binding_ids" in vals:
                    raise AccessError(
                        self.env._("Incoming DTE provenance is server-owned.")
                    )
                company = self.env["res.company"].browse(
                    vals.get("company_id") or self.env.company.id
                )
                partner = self.env["res.partner"].browse(vals.get("partner_id"))
                if (
                    company.dte_inbox_account_enabled
                    and vals.get("move_type", self.env.context.get("default_move_type"))
                    == "in_invoice"
                    and partner
                ):
                    IncomingAccountBindingService(self.env)._lock_suppliers(partner)
        moves = (
            super(AccountMove, self.with_context(**native_context()))
            .create(vals_list)
            .with_env(self.env)
        )
        moves._check_incoming_account_identity()
        return moves

    def write(self, vals):
        if trusted(self.env) or native_guard(self.env):
            return super().write(vals)
        if "dte_inbox_binding_ids" in vals:
            raise AccessError(self.env._("Incoming DTE provenance is server-owned."))
        relevant = {
            "company_id",
            "partner_id",
            "l10n_latam_document_type_id",
            "l10n_latam_document_number",
            "name",
            "move_type",
            "state",
        } & vals.keys()
        for move in self:
            bound = self.env[BINDING].sudo().search_count([("move_id", "=", move.id)])
            if bound and (
                {
                    "company_id",
                    "partner_id",
                    "l10n_latam_document_type_id",
                    "l10n_latam_document_number",
                    "name",
                    "move_type",
                    "invoice_date",
                    "currency_id",
                }
                & vals.keys()
            ):
                # Native inverses may write the same value; changed
                # fiscal identity is forbidden.
                for key in {
                    "company_id",
                    "partner_id",
                    "l10n_latam_document_type_id",
                    "l10n_latam_document_number",
                    "name",
                    "move_type",
                    "invoice_date",
                    "currency_id",
                } & vals.keys():
                    value = move[key]
                    current = (
                        value.id
                        if hasattr(value, "id")
                        else str(value)
                        if key == "invoice_date"
                        else value
                    )
                    if current != vals[key]:
                        raise UserError(
                            self.env._("The bound bill fiscal identity cannot change.")
                        )
            if bound and vals.get("auto_post", "no") != "no":
                raise UserError(
                    self.env._("Incoming DTE bills require explicit native posting.")
                )
            if relevant and (
                bound
                or (
                    move.company_id.dte_inbox_account_enabled
                    and move.move_type == "in_invoice"
                )
            ):
                partners = move.partner_id | self.env["res.partner"].browse(
                    vals.get("partner_id")
                )
                IncomingAccountBindingService(self.env)._lock_suppliers(partners)
        result = super(AccountMove, self.with_context(**native_context())).write(vals)
        if relevant:
            self._check_incoming_account_identity()
        return result

    def unlink(self):
        if self.env[BINDING].sudo().search_count([("move_id", "in", self.ids)]):
            raise UserError(
                self.env._(
                    "Cancel a bound vendor bill instead of deleting its provenance."
                )
            )
        return super().unlink()

    def copy_data(self, default=None):
        values = super().copy_data(default=default)
        for vals in values:
            vals.pop("dte_inbox_binding_ids", None)
            for key in ("line_ids", "invoice_line_ids"):
                for command in vals.get(key, []):
                    if command[0] == 0:
                        command[2].pop("dte_inbox_line_number", None)
        return values

    def _post(self, soft=True):
        for move in self:
            bound = self.env[BINDING].sudo().search_count([("move_id", "=", move.id)])
            if bound or (
                move.move_type == "in_invoice"
                and move.company_id.dte_inbox_account_enabled
            ):
                IncomingAccountBindingService(self.env)._lock_suppliers(move.partner_id)
            if bound:
                move._check_incoming_account_evidence()
        self._check_incoming_account_identity()
        return super()._post(soft=soft)

    def _check_incoming_account_identity(self):
        for move in self:
            if (
                move.move_type != "in_invoice"
                or move.l10n_latam_document_type_id.code not in {"33", "34"}
            ):
                continue
            bound = self.env[BINDING].sudo().search([("move_id", "=", move.id)])
            if not move.company_id.dte_inbox_account_enabled and not bound:
                continue
            folio = canonical_folio(move.l10n_latam_document_number)
            if not folio or not move.commercial_partner_id:
                continue
            identity = dict(
                issuer_vat=vat(move.commercial_partner_id.vat),
                document_code=move.l10n_latam_document_type_id.code,
                folio=folio,
            )
            candidates = IncomingAccountBindingService(self.env)._find_candidates(
                move.company_id, move.commercial_partner_id, dict(identity=identity)
            )
            other_binding = (
                self.env[BINDING]
                .sudo()
                .search_count(
                    [
                        ("company_id", "=", move.company_id.id),
                        ("issuer_vat", "=", identity["issuer_vat"]),
                        ("document_code", "=", identity["document_code"]),
                        ("folio", "=", folio),
                        ("move_id", "!=", move.id),
                    ]
                )
            )
            if set(candidates.ids) - {move.id} or other_binding:
                reject(self.env, "duplicate_bill")

    def _check_incoming_account_evidence(self):
        for move in self:
            binding = self.env[BINDING].search([("move_id", "=", move.id)])
            binding.ensure_one()
            document = IncomingAccountEvidenceService(self.env).read(
                binding.incoming_document_id
            )
            if binding.xml_sha256 != document[
                "xml_sha256"
            ] or binding.source_snapshot != source_snapshot(document):
                reject(self.env, "source_digest_mismatch")
            if (
                vat(move.company_id.vat) != document["identity"]["receiver_vat"]
                or move.company_id.country_id.code != "CL"
                or move.company_id.account_fiscal_country_id.code != "CL"
                or move.company_id.currency_id.name != "CLP"
                or move.company_id.currency_id.rounding != 1
            ):
                reject(self.env, "identity_mismatch")
            IncomingAccountComparisonService(self.env).compare_move(document, move)

    def action_open_incoming_dte(self):
        self.ensure_one()
        self.check_access("read")
        if not self.env.user.has_group(GROUP):
            raise AccessError(
                self.env._("The incoming DTE vendor bill role is required.")
            )
        self.dte_inbox_binding_ids.ensure_one()
        return self.dte_inbox_binding_ids.action_open_incoming()
