# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Durable audit and evidence record for MiPyme DTE recovery."""

import base64
import hashlib
import struct
import unicodedata

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services.identity import (
    DTE_TYPES,
    foreign_identifier_key,
    normalize_folio,
    normalize_rut,
)
from ..services.mipyme_adapter import ProductionMipymeAdapter
from ..services.recovery_service import RecoveryService
from ..services.xml_dte import accounting_data

DTE_SELECTION = [
    (code, code) for code in sorted(DTE_TYPES, key=lambda value: int(value))
]
_RECIPIENT_RESOLUTION_FIELDS = frozenset(
    {
        "recipient_partner_id",
        "recipient_country_id",
        "recipient_resolution_source",
        "recipient_resolution_digest",
        "recipient_resolution_user_id",
        "recipient_resolution_date",
        "recipient_resolution_reason",
        "recipient_resolution_history",
    }
)
_RECIPIENT_RESOLUTION_WRITE_TOKEN = object()
_RECOVERY_EVIDENCE_WRITE_TOKEN = object()
STATUS_SELECTION = [
    ("recovered", "Recovered and associated"),
    ("review", "Recovered - operator review"),
    ("not_found", "Not found"),
    ("multiple_matches", "Multiple matches"),
    ("invalid_xml", "Invalid XML"),
    ("captcha", "Captcha required"),
    ("authentication_failed", "Authentication failed"),
    ("transport_error", "Transport error"),
    ("indeterminate", "Indeterminate portal response"),
    ("rejected", "Rejected by portal"),
    ("production_only", "Production only"),
    ("imported", "Imported to Odoo"),
    ("import_error", "Import error"),
]


class DteRecovery(models.Model):
    _name = "l10n_cl.dte.recovery"
    _description = "Recovered MiPyme DTE"
    _order = "retrieved_at desc, id desc"

    name = fields.Char(compute="_compute_name", store=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    issuer_rut = fields.Char(required=True, index=True, readonly=True)
    dte_type = fields.Selection(DTE_SELECTION, required=True, index=True, readonly=True)
    folio = fields.Integer(required=True, index=True, readonly=True)
    source = fields.Selection(
        [("mipyme", "SII MiPyme")], default="mipyme", required=True, readonly=True
    )
    date_from = fields.Date(required=True, readonly=True)
    date_to = fields.Date(required=True, readonly=True)
    status = fields.Selection(
        STATUS_SELECTION, required=True, readonly=True, index=True
    )
    error_code = fields.Char(readonly=True)
    diagnostic = fields.Text(readonly=True)
    digest = fields.Char(string="SHA-256", readonly=True, index=True)
    retrieved_at = fields.Datetime(
        required=True, readonly=True, default=fields.Datetime.now
    )
    user_id = fields.Many2one(
        "res.users", required=True, readonly=True, default=lambda self: self.env.user
    )
    attachment_id = fields.Many2one(
        "ir.attachment", string="Evidence XML", readonly=True, copy=False
    )
    move_id = fields.Many2one(
        "account.move", string="Invoice", readonly=True, copy=False
    )
    recipient_partner_id = fields.Many2one(
        "res.partner", string="Resolved Recipient", readonly=True, copy=False
    )
    recipient_country_id = fields.Many2one(
        "res.country", string="Resolved Recipient Country", readonly=True, copy=False
    )
    recipient_resolution_source = fields.Selection(
        [
            ("xml", "XML country"),
            ("existing_partner", "Existing customer"),
            ("operator", "Operator confirmation"),
        ],
        readonly=True,
        copy=False,
    )
    recipient_resolution_digest = fields.Char(readonly=True, copy=False, index=True)
    recipient_resolution_user_id = fields.Many2one(
        "res.users", readonly=True, copy=False
    )
    recipient_resolution_date = fields.Datetime(readonly=True, copy=False)
    recipient_resolution_reason = fields.Text(readonly=True, copy=False)
    recipient_resolution_history = fields.Json(readonly=True, copy=False, default=list)
    evidence_url = fields.Char(compute="_compute_evidence_url")

    _sql_constraints = [
        (
            "recovery_identity_digest_unique",
            "unique(company_id, dte_type, folio, source, digest)",
            (
                "A recovery with the same company, document identity and digest already"
                "exists."
            ),
        ),
        (
            "recovery_folio_positive",
            "check(folio > 0)",
            "The DTE folio must be positive.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if any(_RECIPIENT_RESOLUTION_FIELDS.intersection(vals) for vals in vals_list):
            raise AccessError(
                _(
                    "La resolución del receptor sólo puede guardarse desde el flujo"
                    "MiPyme."
                )
            )
        return super().create(vals_list)

    def write(self, vals):
        if {"digest", "attachment_id"}.intersection(vals) and self.env.context.get(
            "_mipyme_evidence_write_token"
        ) is not _RECOVERY_EVIDENCE_WRITE_TOKEN:
            raise AccessError(
                _("La evidencia fiscal de la recuperación no se puede reemplazar.")
            )
        if (
            _RECIPIENT_RESOLUTION_FIELDS.intersection(vals)
            and self.env.context.get("_mipyme_recipient_resolution_token")
            is not _RECIPIENT_RESOLUTION_WRITE_TOKEN
        ):
            raise AccessError(
                _(
                    "La resolución del receptor sólo puede cambiarse desde el flujo"
                    "MiPyme."
                )
            )
        return super().write(vals)

    @api.depends("issuer_rut", "dte_type", "folio")
    def _compute_name(self):
        for record in self:
            record.name = (
                f"{record.issuer_rut or ''} / DTE {record.dte_type or ''} / "
                f"{record.folio or ''}"
            )

    @api.depends("attachment_id")
    def _compute_evidence_url(self):
        for record in self:
            record.evidence_url = (
                f"/web/content/{record.attachment_id.id}?download=true"
                if record.attachment_id
                else False
            )

    @api.constrains("issuer_rut", "dte_type", "folio")
    def _check_identity(self):
        for record in self:
            try:
                normalize_rut(record.issuer_rut)
                normalize_folio(record.folio)
            except ValueError as error:
                raise ValidationError(str(error)) from error

    @api.model
    def recover_issued_document(
        self,
        company,
        issuer_rut,
        dte_type,
        folio,
        date_from,
        date_to,
        adapter=None,
    ):
        """Recover one issued DTE for an authorized company without a wizard."""
        if not self.env.user.has_group(
            "l10n_cl_dte_mipyme_recovery.group_mipyme_recovery_user"
        ):
            raise AccessError(_("No tiene permiso para recuperar DTE desde MiPyme."))
        if company not in self.env.companies:
            raise AccessError(_("La compañía no está habilitada para este usuario."))
        if company.country_id.code != "CL":
            raise ValidationError(
                _("La recuperación MiPyme requiere una compañía chilena.")
            )
        try:
            if normalize_rut(issuer_rut) != normalize_rut(company.vat):
                raise ValidationError(_("El emisor no coincide con la compañía."))
        except ValueError as error:
            raise ValidationError(str(error)) from error
        RecoveryService._validate_dates(date_from, date_to)
        result = RecoveryService(adapter or ProductionMipymeAdapter(self.env)).recover(
            company, issuer_rut, dte_type, folio, date_from, date_to
        )
        return self.persist_result(result, company, date_from, date_to)

    @api.model
    def _lock_identity(self, company_id, dte_type, folio):
        """Serialize persistence and invoice creation for one fiscal identity."""
        key = f"mipyme:{company_id}:{dte_type}:{folio}".encode()
        first, second = struct.unpack("!ii", hashlib.sha256(key).digest()[:8])
        self.env.cr.execute("SELECT pg_advisory_xact_lock(%s, %s)", (first, second))

    @api.model
    def persist_result(self, result, company, date_from, date_to):
        """Persist one result idempotently.

        Only a unique invoice relation is written.
        """
        identity = result.identity
        self._lock_identity(company.id, identity.dte_type, identity.folio)
        existing = self.search(
            [
                ("company_id", "=", company.id),
                ("dte_type", "=", identity.dte_type),
                ("folio", "=", identity.folio),
                ("source", "=", "mipyme"),
                ("digest", "=", result.digest),
            ],
            limit=1,
        )
        if existing:
            return existing

        values = {
            "company_id": company.id,
            "issuer_rut": identity.issuer_rut,
            "dte_type": identity.dte_type,
            "folio": identity.folio,
            "date_from": date_from,
            "date_to": date_to,
            "status": result.status,
            "error_code": result.error_code,
            "diagnostic": result.diagnostic,
            "digest": result.digest or False,
            "retrieved_at": fields.Datetime.now(),
            "user_id": self.env.user.id,
        }
        with self.env.cr.savepoint():
            recovery = self.create(values)
            if result.status != "recovered" or not result.candidate:
                return recovery
            attachment = (
                self.env["ir.attachment"]
                .sudo()
                .create(
                    {
                        "name": f"DTE_{identity.dte_type}_{identity.folio}.xml",
                        "res_model": self._name,
                        "res_id": recovery.id,
                        "type": "binary",
                        # The bytes have already been validated.  base64 is only the
                        # Odoo transport encoding; it does not rewrite the XML.
                        "datas": base64.b64encode(result.candidate.raw_xml),
                        "mimetype": "application/xml",
                    }
                )
            )
            recovery.sudo().with_context(
                _mipyme_evidence_write_token=_RECOVERY_EVIDENCE_WRITE_TOKEN
            ).write({"attachment_id": attachment.id})
            try:
                data = accounting_data(result.candidate)
            except ValueError:
                data = None
            resolution = False
            if data and identity.dte_type in ("110", "111"):
                resolution = self._resolve_export_recipient(data, company)
                if resolution["state"] in ("resolved", "create"):
                    recovery._store_recipient_resolution(
                        resolution["partner"],
                        resolution["country"],
                        resolution["source"],
                        result.digest,
                    )
            identity_moves = self._matching_moves(company, identity)
            moves = identity_moves.filtered(
                lambda move: self._recipient_matches_move(move, data, identity.dte_type)
            )
            if len(identity_moves) == 1 and len(moves) == 1:
                move = moves[0]
                attachment.sudo().write(
                    {"res_model": "account.move", "res_id": move.id}
                )
                if "l10n_cl_dte_file" in move._fields:
                    move.sudo().l10n_cl_dte_file = attachment.id
                recovery.sudo().write({"move_id": move.id, "status": "recovered"})
            elif len(identity_moves) > 1 or len(moves) > 1:
                recovery.sudo().write(
                    {
                        "status": "review",
                        "error_code": "INVOICE_AMBIGUOUS",
                        "diagnostic": (
                            "El XML es válido, pero hay varias facturas "
                            "coincidentes en Odoo."
                        ),
                    }
                )
            elif identity_moves:
                recovery.sudo().write(
                    {
                        "status": "review",
                        "error_code": "INVOICE_RECIPIENT_MISMATCH",
                        "diagnostic": (
                            "Existe una factura con tipo y folio coincidentes, "
                            "pero otro receptor."
                        ),
                    }
                )
            elif resolution and resolution["state"] in (
                "confirmation_required",
                "invalid",
            ):
                recovery.sudo().write(
                    {
                        "status": "review",
                        "error_code": "RECIPIENT_RESOLUTION_REQUIRED",
                        "diagnostic": resolution["message"],
                    }
                )
            else:
                recovery.sudo().write(
                    {
                        "status": "review",
                        "error_code": "INVOICE_NOT_FOUND",
                        "diagnostic": (
                            "El XML es válido, pero no existe una factura "
                            "única en Odoo."
                        ),
                    }
                )
            return recovery

    def _matching_moves(self, company, identity):
        moves = self.env["account.move"].search(
            [
                ("company_id", "=", company.id),
                ("move_type", "in", ("out_invoice", "out_refund")),
                ("l10n_latam_document_type_id.code", "=", int(identity.dte_type)),
            ]
        )
        moves = moves.filtered(
            lambda move: (
                _folio_as_int(move.l10n_latam_document_number) == identity.folio
            )
        )
        return moves

    def _recipient_matches_move(self, move, data, dte_type):
        if not data:
            return False
        if int(dte_type) in (110, 111):
            partner = self._export_partner_for_move(data, move.company_id)
            return bool(partner and move.partner_id == partner)
        return self._same_rut(move.partner_id.vat, data.recipient_rut)

    @staticmethod
    def _normalized_recipient_name(value):
        normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
        return "".join(character for character in normalized if character.isalnum())

    def _export_country_from_xml(self, data):
        if not data.recipient_country_code:
            return self.env["res.country"]
        countries = self.env["res.country"].search(
            [("l10n_cl_customs_code", "=", data.recipient_country_code)], limit=2
        )
        if len(countries) != 1 or countries.code == "CL":
            return self.env["res.country"]
        return countries

    def _export_partner_candidates(self, company, data, country=None):
        domain = [
            ("active", "=", True),
            ("vat", "!=", False),
            ("country_id", "!=", False),
            ("country_id.code", "!=", "CL"),
            ("company_id", "in", [False, company.id]),
        ]
        if country:
            domain.append(("country_id", "=", country.id))
        partners = self.env["res.partner"].search(domain)
        matches = partners.filtered(
            lambda partner: foreign_identifier_key(partner.vat, partner.country_id.code)
            == foreign_identifier_key(
                data.recipient_foreign_id, partner.country_id.code
            )
        )
        eligible = matches.filtered(
            lambda partner: not partner.parent_id and partner.is_company
        )
        return matches, eligible

    def _resolve_export_recipient(self, data, company):
        """Resolve a foreign recipient without creating or editing a partner."""
        if not data.recipient_foreign_id or not data.recipient_foreign_id.strip():
            return {
                "state": "invalid",
                "country": self.env["res.country"],
                "partner": self.env["res.partner"],
                "candidates": self.env["res.partner"],
                "source": False,
                "message": _(
                    "El XML de exportación no informa el identificador extranjero."
                ),
            }

        country = self._export_country_from_xml(data)
        if data.recipient_country_code and not country:
            return {
                "state": "invalid",
                "country": country,
                "partner": self.env["res.partner"],
                "candidates": self.env["res.partner"],
                "source": False,
                "message": _(
                    "El país declarado para el receptor extranjero no es inequívoco."
                ),
            }

        matches, eligible = self._export_partner_candidates(
            company, data, country=country if data.recipient_country_code else None
        )
        if len(eligible) == 1 and country:
            return {
                "state": "resolved",
                "country": country,
                "partner": eligible,
                "candidates": eligible,
                "source": "xml",
                "message": False,
            }
        if len(eligible) == 1 and not country:
            partner = eligible
            if self._normalized_recipient_name(data.recipient_name) and (
                self._normalized_recipient_name(data.recipient_name)
                == self._normalized_recipient_name(partner.name)
            ):
                return {
                    "state": "resolved",
                    "country": partner.country_id,
                    "partner": partner,
                    "candidates": eligible,
                    "source": "existing_partner",
                    "message": False,
                }

        if country and not matches:
            return {
                "state": "create",
                "country": country,
                "partner": self.env["res.partner"],
                "candidates": self.env["res.partner"],
                "source": "xml",
                "message": False,
            }

        if matches and not eligible:
            message = _(
                "El identificador coincide con un contacto que no es una empresa raíz;"
                "revise el cliente."
            )
            candidates = eligible
        elif not country and not matches:
            message = _(
                "El XML omite el país y no hay un cliente accesible que confirme su"
                "identidad."
            )
            candidates = eligible
        else:
            message = _(
                "Seleccione y confirme el cliente extranjero compatible con el XML."
            )
            candidates = eligible
        return {
            "state": "confirmation_required",
            "country": country,
            "partner": self.env["res.partner"],
            "candidates": candidates,
            "source": False,
            "message": message,
        }

    def _export_partner_for_move(self, data, company):
        if len(self) == 1 and self.recipient_partner_id:
            partner = self.recipient_partner_id
            if self._stored_recipient_resolution_is_current(data, company):
                return partner
            return self.env["res.partner"]
        resolution = self._resolve_export_recipient(data, company)
        return (
            resolution["partner"]
            if resolution["state"] == "resolved"
            else self.env["res.partner"]
        )

    def _recipient_partner_snapshot(self, partner):
        return {
            "id": partner.id,
            "vat": partner.vat or "",
            "country_id": partner.country_id.id,
            "name": partner.name or "",
            "parent_id": partner.parent_id.id,
            "company_id": partner.company_id.id,
            "active": partner.active,
        }

    def _stored_recipient_resolution_is_current(self, data, company):
        self.ensure_one()
        if (
            not self.recipient_partner_id
            or not self.digest
            or self.recipient_resolution_digest != self.digest
        ):
            return False
        event = next(
            (
                item
                for item in reversed(self.recipient_resolution_history or [])
                if item.get("event") == "resolved"
                and item.get("digest") == self.digest
                and item.get("partner_id") == self.recipient_partner_id.id
            ),
            None,
        )
        if not event or event.get("partner") != self._recipient_partner_snapshot(
            self.recipient_partner_id
        ):
            return False
        return self._partner_matches_export_identity(
            self.recipient_partner_id, data, company
        )

    def _partner_matches_export_identity(self, partner, data, company):
        if (
            not partner.exists()
            or not partner.active
            or partner.parent_id
            or not partner.is_company
            or not partner.country_id
            or partner.country_id.code == "CL"
            or (partner.company_id and partner.company_id != company)
        ):
            return False
        if data.recipient_country_code:
            country = self._export_country_from_xml(data)
            if not country or partner.country_id != country:
                return False
        return bool(
            data.recipient_foreign_id
            and foreign_identifier_key(partner.vat, partner.country_id.code)
            == foreign_identifier_key(
                data.recipient_foreign_id, partner.country_id.code
            )
        )

    def _store_recipient_resolution(
        self, partner, country, source, digest, reason=False
    ):
        self.ensure_one()
        now = fields.Datetime.now()
        history = list(self.recipient_resolution_history or [])
        history.append(
            {
                "event": "resolved",
                "digest": digest,
                "source": source,
                "partner_id": partner.id if partner else False,
                "partner": self._recipient_partner_snapshot(partner)
                if partner
                else False,
                "country_id": country.id if country else False,
                "country_code": country.code if country else False,
                "actor_id": self.env.user.id,
                "date": fields.Datetime.to_string(now),
                "reason": reason or False,
            }
        )
        values = {
            "recipient_partner_id": partner.id if partner else False,
            "recipient_country_id": country.id if country else False,
            "recipient_resolution_source": source,
            "recipient_resolution_digest": digest,
            "recipient_resolution_user_id": self.env.user.id,
            "recipient_resolution_date": now,
            "recipient_resolution_reason": reason or False,
            "recipient_resolution_history": history,
        }
        self._write_recipient_resolution(values)

    def _invalidate_recipient_resolution(self, reason):
        self.ensure_one()
        if not self.recipient_resolution_digest:
            return
        history = list(self.recipient_resolution_history or [])
        history.append(
            {
                "event": "invalidated",
                "digest": self.recipient_resolution_digest,
                "partner_id": self.recipient_partner_id.id or False,
                "actor_id": self.env.user.id,
                "date": fields.Datetime.to_string(fields.Datetime.now()),
                "reason": reason,
            }
        )
        self._write_recipient_resolution(
            {
                "recipient_partner_id": False,
                "recipient_country_id": False,
                "recipient_resolution_source": False,
                "recipient_resolution_digest": False,
                "recipient_resolution_user_id": False,
                "recipient_resolution_date": False,
                "recipient_resolution_reason": False,
                "recipient_resolution_history": history,
            }
        )

    def _write_recipient_resolution(self, values):
        self.sudo().with_context(
            _mipyme_recipient_resolution_token=_RECIPIENT_RESOLUTION_WRITE_TOKEN
        ).write(values)

    def action_import_to_odoo(self):
        """Reconstruct an already-issued DTE without running SII emission."""
        self.ensure_one()
        if not self.env.user.has_group(
            "l10n_cl_dte_mipyme_recovery.group_mipyme_recovery_user"
        ):
            raise AccessError(_("No tiene permiso para importar DTE desde MiPyme."))
        self.check_access("read")
        if self.company_id not in self.env.companies:
            raise AccessError(_("La compañía no está habilitada para este usuario."))
        self._lock_identity(self.company_id.id, self.dte_type, self.folio)
        self.invalidate_recordset(["move_id", "status"])
        if self.move_id:
            return self.move_id
        if (
            self.status not in ("recovered", "review", "import_error")
            or not self.attachment_id
        ):
            raise UserError(_("Sólo se pueden importar recuperaciones con XML válido."))
        if self.status == "review" and self.error_code not in (
            "INVOICE_NOT_FOUND",
            "RECIPIENT_RESOLUTION_REQUIRED",
            "",
        ):
            raise UserError(_("La recuperación requiere revisión antes de importar."))
        candidate, data = self._accounting_data_from_attachment()
        if not data.recipient_rut:
            raise UserError(_("El XML no contiene RUT del receptor."))
        if int(self.dte_type) in (110, 111):
            resolution = self._prepare_export_recipient(data)
            if resolution["state"] in ("confirmation_required", "invalid"):
                self.sudo().write(
                    {
                        "status": "review",
                        "error_code": "RECIPIENT_RESOLUTION_REQUIRED",
                        "diagnostic": resolution["message"],
                    }
                )
                return self.action_open_recipient_resolution()
        identity_moves = self._matching_moves(self.company_id, self._identity())
        existing = identity_moves.filtered(
            lambda move: self._recipient_matches_move(move, data, self.dte_type)
        )
        if len(identity_moves) > 1:
            raise UserError(_("Hay varias facturas con el mismo tipo y folio."))
        if identity_moves and not existing:
            raise UserError(
                _(
                    "Existe una factura con tipo y folio coincidentes, pero otro"
                    "receptor."
                )
            )
        if existing:
            self.sudo().write({"move_id": existing.id, "status": "recovered"})
            return existing
        try:
            with self.env.cr.savepoint():
                move = self._create_imported_move(data)
                self.attachment_id.sudo().write(
                    {"res_model": "account.move", "res_id": move.id}
                )
                if "l10n_cl_dte_file" in move._fields:
                    move.sudo().write({"l10n_cl_dte_file": self.attachment_id.id})
                self.sudo().write(
                    {
                        "move_id": move.id,
                        "status": "imported",
                        "error_code": False,
                        "diagnostic": (
                            "DTE recuperado como factura borrador; complete la "
                            "analítica y confirme. No se reemitirá al SII."
                        ),
                    }
                )
        except Exception as error:
            self.sudo().write(
                {
                    "status": "import_error",
                    "error_code": "IMPORT_FAILED",
                    "diagnostic": str(error),
                }
            )
            raise
        return move

    def _identity(self):
        from ..services.identity import DteIdentity

        return DteIdentity(self.issuer_rut, self.dte_type, self.folio)

    def _accounting_data_from_attachment(self):
        self.ensure_one()
        from ..services.xml_dte import extract_candidates

        payload = base64.b64decode(self.attachment_id.datas)
        actual_digest = hashlib.sha256(payload).hexdigest()
        if not self.digest or actual_digest != self.digest:
            raise UserError(
                _("El hash del XML almacenado no coincide con su evidencia.")
            )
        candidate = next(
            (
                item
                for item in extract_candidates(payload)
                if item.identity == self._identity()
            ),
            None,
        )
        if not candidate or candidate.digest != self.digest:
            raise UserError(
                _("El XML almacenado ya no coincide con la identidad recuperada.")
            )
        return candidate, accounting_data(candidate)

    def _prepare_export_recipient(self, data):
        self.ensure_one()
        if self.recipient_resolution_digest:
            if self.recipient_resolution_digest == self.digest:
                if (
                    self.recipient_partner_id
                    and self._stored_recipient_resolution_is_current(
                        data, self.company_id
                    )
                ):
                    return {
                        "state": "resolved",
                        "country": self.recipient_country_id,
                        "partner": self.recipient_partner_id,
                        "candidates": self.recipient_partner_id,
                        "source": self.recipient_resolution_source,
                        "message": False,
                    }
                if (
                    not self.recipient_partner_id
                    and self.recipient_resolution_source == "xml"
                    and self.recipient_country_id
                ):
                    current = self._resolve_export_recipient(data, self.company_id)
                    if (
                        current["state"] == "create"
                        and current["country"] == self.recipient_country_id
                    ):
                        return current
            self._invalidate_recipient_resolution(
                _(
                    "Cambió el XML o los datos de identidad del cliente desde la"
                    "resolución."
                )
            )

        resolution = self._resolve_export_recipient(data, self.company_id)
        if resolution["state"] in ("resolved", "create"):
            self._store_recipient_resolution(
                resolution["partner"],
                resolution["country"],
                resolution["source"],
                self.digest,
            )
        return resolution

    def action_open_recipient_resolution(self):
        self.ensure_one()
        if not self.env.user.has_group(
            "l10n_cl_dte_mipyme_recovery.group_mipyme_recovery_user"
        ):
            raise AccessError(_("No tiene permiso para resolver el receptor MiPyme."))
        self.check_access("read")
        if self.company_id not in self.env.companies:
            raise AccessError(_("La compañía no está habilitada para este usuario."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Resolver receptor de exportación"),
            "res_model": "l10n_cl.dte.recovery.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_recovery_id": self.id},
        }

    def action_confirm_recipient(self, partner_id, reason):
        """Persist an operator-selected foreign customer after server checks."""
        self.ensure_one()
        if not self.env.user.has_group(
            "l10n_cl_dte_mipyme_recovery.group_mipyme_recovery_user"
        ):
            raise AccessError(_("No tiene permiso para confirmar el receptor MiPyme."))
        self.check_access("read")
        if self.company_id not in self.env.companies:
            raise AccessError(_("La compañía no está habilitada para este usuario."))
        reason = str(reason or "").strip()
        if len(reason) < 5:
            raise UserError(_("Indique un motivo de al menos cinco caracteres."))
        self._lock_identity(self.company_id.id, self.dte_type, self.folio)
        _candidate, data = self._accounting_data_from_attachment()
        if int(self.dte_type) not in (110, 111):
            raise UserError(
                _("La confirmación de receptor sólo aplica a exportaciones.")
            )
        if not data.recipient_foreign_id or not data.recipient_foreign_id.strip():
            raise UserError(
                _("El XML no contiene identificador extranjero para confirmar.")
            )
        try:
            partner_id = int(partner_id)
        except (TypeError, ValueError) as error:
            raise UserError(
                _("El cliente seleccionado ya no está disponible.")
            ) from error
        partner = self.env["res.partner"].browse(partner_id).exists()
        if not partner:
            raise UserError(_("El cliente seleccionado ya no está disponible."))
        partner.check_access("read")
        if not self._partner_matches_export_identity(partner, data, self.company_id):
            raise UserError(
                _("El cliente no coincide con el identificador y país del XML.")
            )
        self._store_recipient_resolution(
            partner,
            partner.country_id,
            "operator",
            self.digest,
            reason=reason,
        )
        self.sudo().write(
            {
                "status": "review",
                "error_code": "INVOICE_NOT_FOUND",
                "diagnostic": _(
                    "Receptor extranjero confirmado; continúe con la validación fiscal."
                ),
            }
        )
        return True

    def _create_imported_move(self, data):
        document_type = (
            self.env["l10n_latam.document.type"]
            .with_context(active_test=False)
            .search(
                [("code", "=", int(self.dte_type)), ("country_id.code", "=", "CL")],
                limit=1,
            )
        )
        if not document_type:
            raise UserError(
                f"No existe configuración LATAM para el DTE {self.dte_type}."
            )
        if document_type.internal_type not in ("invoice", "debit_note"):
            raise UserError(_("El tipo DTE no corresponde a una factura de venta."))
        if not document_type.active:
            raise UserError(
                f"El tipo DTE {self.dte_type} está desactivado en la configuración "
                "LATAM; actívelo antes de importar la factura."
            )
        currency = self._currency_from_xml(data)
        if int(self.dte_type) in (110, 111):
            partner = self._export_recipient_partner(data)
        else:
            partner = self._resolve_recipient_partner(data)
            if not partner:
                partner = self._create_recipient_partner(data)
            else:
                self._complete_recipient_partner(partner, data)
        journal = self.env["account.journal"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("type", "=", "sale"),
                ("l10n_latam_use_documents", "=", True),
            ],
            limit=1,
        )
        if not journal:
            raise UserError(
                _("No existe un diario de ventas chileno con documentos LATAM.")
            )
        account = journal.default_account_id or self.company_id.account_sale_id
        if not account:
            raise UserError(
                _("El diario de ventas no tiene cuenta de ingresos configurada.")
            )
        lines = self._invoice_lines_from_xml(data, account)
        references = self._invoice_references_from_xml(data, document_type)
        values = {
            "move_type": "out_invoice",
            "company_id": self.company_id.id,
            "partner_id": partner.id,
            "journal_id": journal.id,
            "invoice_date": data.issue_date,
            "currency_id": currency.id,
            "l10n_latam_document_type_id": document_type.id,
            "l10n_latam_document_number": str(self.folio),
            "l10n_cl_mipyme_imported": True,
            "l10n_cl_mipyme_xml_total": data.total_amount,
            "l10n_cl_dte_status": "manual",
            "invoice_line_ids": lines,
            "l10n_cl_reference_ids": references,
        }
        # Fields owned by the optional DTE localization are written only when it
        # is installed, so the recovery works in a plain accounting database.
        move_fields = self.env["account.move"]._fields
        for field_name in ("l10n_cl_dte_status", "l10n_cl_reference_ids"):
            if field_name not in move_fields:
                values.pop(field_name, None)
        move = (
            self.env["account.move"]
            .with_context(l10n_cl_mipyme_recovery_import=True)
            .create(values)
        )
        currency = move.currency_id
        if (
            not currency.is_zero(move.amount_total - data.total_amount)
            or not currency.is_zero(
                move.amount_untaxed - data.net_amount - data.exempt_amount
            )
            or not currency.is_zero(move.amount_tax - data.vat_amount)
        ):
            raise UserError(
                _(
                    "Los importes contables no coinciden con el XML; revise impuestos y"
                    "líneas."
                )
            )
        if (
            currency != self.company_id.currency_id
            and not self.company_id.currency_id.is_zero(
                abs(move.amount_total_signed) - data.company_total_amount
            )
        ):
            raise UserError(
                _(
                    "El tipo de cambio histórico de Odoo no reproduce el total "
                    "en moneda nacional del XML."
                )
            )
        return move

    def _currency_from_xml(self, data):
        if not data.currency_name:
            return self.company_id.currency_id
        normalize = self.env["account.move"]._l10n_cl_normalize_currency_name
        currencies = self.env["res.currency"].search([("active", "=", True)])
        matches = currencies.filtered(
            lambda candidate: normalize(candidate.name) == data.currency_name.upper()
        )
        if len(matches) != 1:
            raise UserError(
                _("La moneda del XML no tiene una equivalencia única y activa.")
            )
        currency = matches[0]
        if currency != self.company_id.currency_id:
            expected_other = normalize(self.company_id.currency_id.name)
            if (
                data.company_currency_name.upper() != expected_other
                or not data.company_total_amount
            ):
                raise UserError(
                    _(
                        "El XML en moneda extranjera no informa un total "
                        "comparable en la moneda de la compañía."
                    )
                )
        return currency

    def _export_recipient_partner(self, data):
        resolution = self._prepare_export_recipient(data)
        if resolution["state"] == "resolved":
            return resolution["partner"]
        if resolution["state"] != "create":
            raise UserError(
                resolution["message"] or "Debe resolver el receptor extranjero."
            )
        country = resolution["country"]
        partner = self.env["res.partner"].create(
            {
                "name": data.recipient_name or data.recipient_foreign_id,
                "vat": data.recipient_foreign_id,
                "country_id": country.id,
                "company_type": "company",
                "l10n_cl_sii_taxpayer_type": "4",
                "street": data.recipient_street or False,
                "city": data.recipient_city or False,
            }
        )
        self._store_recipient_resolution(partner, country, "xml", self.digest)
        return partner

    def _invoice_lines_from_xml(self, data, account):
        currency = self.company_id.currency_id
        tax = self._sale_tax_from_xml(data)
        line_total = sum(line["amount"] for line in data.details)
        net_total = data.net_amount + data.exempt_amount
        gross_lines = (
            bool(data.vat_amount)
            and currency.is_zero(line_total - data.total_amount)
            and not currency.is_zero(line_total - net_total)
        )
        if not gross_lines and not currency.is_zero(line_total - net_total):
            raise UserError(
                _("Las líneas del XML no suman el neto y exento informados.")
            )
        if (
            data.net_amount
            and data.exempt_amount
            and not any(line["exempt"] for line in data.details)
        ):
            raise UserError(_("El XML mixto no identifica sus líneas exentas."))
        lines = []
        for line in data.details:
            exempt = line["exempt"] or not data.net_amount
            taxable = not exempt and bool(data.vat_amount)
            price = line["price_unit"]
            if gross_lines and taxable:
                price /= 1 + tax.amount / 100
            lines.append(
                (
                    0,
                    0,
                    {
                        "name": line["name"],
                        "quantity": line["quantity"],
                        "price_unit": price,
                        "account_id": account.id,
                        "tax_ids": [(6, 0, [tax.id] if taxable else [])],
                    },
                )
            )
        return lines

    def _sale_tax_from_xml(self, data):
        if not data.vat_amount:
            return self.env["account.tax"]
        if not data.net_amount:
            raise UserError(_("El XML informa IVA sin monto neto."))
        rate = 100 * data.vat_amount / data.net_amount
        domain = [
            ("company_id", "=", self.company_id.id),
            ("type_tax_use", "=", "sale"),
            ("amount_type", "=", "percent"),
            ("price_include", "=", False),
            ("active", "=", True),
        ]
        taxes = (
            self.env["account.tax"]
            .search(domain)
            .filtered(lambda candidate: abs(candidate.amount - rate) < 0.5)
        )
        default = self.company_id.account_sale_tax_id
        if default in taxes:
            return default
        if len(taxes) == 1:
            return taxes
        raise UserError(_("No hay un impuesto de venta único para el IVA del XML."))

    def _invoice_references_from_xml(self, data, document_type):
        if document_type.internal_type == "debit_note" and not data.references:
            raise UserError(
                _("La nota de débito no contiene referencia al documento original.")
            )
        references = []
        for reference in data.references:
            if not reference["folio"] or not reference["date"]:
                raise UserError(_("Una referencia del XML carece de folio o fecha."))
            document = self.env["l10n_latam.document.type"].search(
                [
                    ("code", "=", reference["document_type"]),
                    ("country_id.code", "=", "CL"),
                ],
                limit=1,
            )
            if not document:
                raise UserError(_("Una referencia del XML tiene un tipo desconocido."))
            references.append(
                (
                    0,
                    0,
                    {
                        "origin_doc_number": reference["folio"],
                        "date": reference["date"],
                        "l10n_cl_reference_doc_type_id": document.id,
                        "reference_doc_code": reference["code"] or False,
                        "reason": reference["reason"] or False,
                    },
                )
            )
        return references

    def _resolve_recipient_partner(self, data):
        """Resolve the fiscal entity, ignoring child contacts sharing its RUT."""
        partners = (
            self.env["res.partner"]
            .search([("vat", "!=", False)])
            .filtered(lambda partner: self._same_rut(partner.vat, data.recipient_rut))
        )
        roots = partners.filtered(
            lambda partner: not partner.parent_id and partner.is_company
        )
        if len(roots) > 1:
            raise UserError(_("Hay más de una empresa raíz con el RUT del receptor."))
        if roots:
            return roots[0]
        if len(partners) == 1:
            return partners[0]
        if len(partners) > 1:
            raise UserError(
                _("Hay varios clientes sin empresa raíz con el RUT del receptor.")
            )
        return self.env["res.partner"]

    @staticmethod
    def _same_rut(left, right):
        try:
            return normalize_rut(left) == normalize_rut(right)
        except ValueError:
            return False

    def _recipient_values(self, data):
        return {
            "name": data.recipient_name or data.recipient_rut,
            "vat": data.recipient_rut,
            "street": data.recipient_street or False,
            "city": data.recipient_city or False,
            "country_id": self.env.ref("base.cl").id,
            "l10n_latam_identification_type_id": self.env.ref("l10n_cl.it_RUT").id,
            "l10n_cl_sii_taxpayer_type": "1",
            "l10n_cl_activity_description": data.recipient_activity or False,
        }

    def _create_recipient_partner(self, data):
        return self.env["res.partner"].create(self._recipient_values(data))

    def _complete_recipient_partner(self, partner, data):
        values = self._recipient_values(data)
        missing = {
            field: value
            for field, value in values.items()
            if value and not partner[field]
        }
        if missing:
            partner.write(missing)
        return partner


def _folio_as_int(value):
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return None
