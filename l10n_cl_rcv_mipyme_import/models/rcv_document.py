# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""One-click reconstruction of missing RCV sales from MiPyme XML."""

import calendar
import re
from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.l10n_cl_dte_mipyme_recovery.services.identity import (
    DTE_TYPES,
    normalize_folio,
    normalize_rut,
)
from odoo.addons.l10n_cl_dte_mipyme_recovery.services.xml_dte import (
    accounting_data,
    extract_candidates,
)


class RcvDocument(models.Model):
    _inherit = "l10n.cl.rcv.document"

    mipyme_import_available = fields.Boolean(compute="_compute_mipyme_import_available")

    @api.depends("operation", "reconciliation_state", "move_id", "document_type_code")
    def _compute_mipyme_import_available(self):
        authorized = self.env.user.has_group("l10n_cl_rcv.group_rcv_manager") and (
            self.env.user.has_group(
                "l10n_cl_dte_mipyme_recovery.group_mipyme_recovery_user"
            )
        )
        for document in self:
            document.mipyme_import_available = (
                authorized and document._mipyme_eligible()
            )

    def _mipyme_eligible(self):
        self.ensure_one()
        if (
            self.operation != "sale"
            or self.reconciliation_state != "missing"
            or self.move_id
            or self.document_type_code not in DTE_TYPES
        ):
            return False
        document_type = (
            self.env["l10n_latam.document.type"]
            .with_context(active_test=False)
            .search(
                [
                    ("code", "=", self.document_type_code),
                    ("country_id.code", "=", "CL"),
                ],
                limit=1,
            )
        )
        return document_type.internal_type in ("invoice", "debit_note")

    def _mipyme_search_dates(self):
        self.ensure_one()
        if self.document_date:
            return self.document_date, self.document_date
        period = self.tax_period or ""
        if not re.fullmatch(r"\d{6}", period):
            raise UserError(_("El documento RCV no tiene fecha ni período válido."))
        year, month = int(period[:4]), int(period[4:])
        if year < 2000 or month not in range(1, 13):
            raise UserError(_("El período tributario del RCV no es válido."))
        return date(year, month, 1), date(
            year, month, calendar.monthrange(year, month)[1]
        )

    def action_import_from_mipyme(self):
        """Recover, import, reconcile and open one issued customer invoice."""
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_rcv.group_rcv_manager") or not (
            self.env.user.has_group(
                "l10n_cl_dte_mipyme_recovery.group_mipyme_recovery_user"
            )
        ):
            raise AccessError(
                _("No tiene permisos para importar ventas RCV desde MiPyme.")
            )
        self.check_access("write")
        if self.company_id not in self.env.companies:
            raise AccessError(
                _("La compañía del RCV no está habilitada para este usuario.")
            )
        if (
            self.operation == "sale"
            and self.move_id
            and self.move_id.l10n_cl_mipyme_imported
            and self.move_id.move_type == "out_invoice"
        ):
            return self._open_mipyme_move(self.move_id)
        if not self._mipyme_eligible():
            raise UserError(
                _("Sólo se importan ventas faltantes que son facturas de cliente.")
            )
        self.env["account.move"].check_access("create")
        self.env["l10n_cl.dte.recovery"].check_access("create")
        try:
            issuer = normalize_rut(self.issuer_vat)
            if issuer != normalize_rut(self.company_id.vat):
                raise UserError(_("El emisor RCV no coincide con la compañía."))
            counterpart = normalize_rut(self.counterpart_vat)
            folio = normalize_folio(self.folio)
        except ValueError as error:
            raise UserError(str(error)) from error
        date_from, date_to = self._mipyme_search_dates()
        recovery = self.env["l10n_cl.dte.recovery"].recover_issued_document(
            self.company_id,
            issuer,
            self.document_type_code,
            folio,
            date_from,
            date_to,
        )
        if not recovery.attachment_id:
            return self._open_mipyme_recovery(recovery)
        if recovery.status == "review" and recovery.error_code not in (
            "INVOICE_NOT_FOUND",
            "RECIPIENT_RESOLUTION_REQUIRED",
            False,
        ):
            return self._open_mipyme_recovery(recovery)
        try:
            candidates = extract_candidates(recovery.attachment_id.raw)
            candidate = next(
                (item for item in candidates if item.identity == recovery._identity()),
                None,
            )
            data = accounting_data(candidate) if candidate else None
            recipient = data.recipient_rut if data else ""
            recipient_matches = normalize_rut(recipient) == counterpart
            move_matches = not recovery.move_id or (
                recovery.move_id.move_type == "out_invoice"
                and recovery._recipient_matches_move(
                    recovery.move_id, data, recovery.dte_type
                )
            )
        except ValueError:
            recipient_matches = move_matches = False
        if not recipient_matches or not move_matches:
            recovery.sudo().write(
                {
                    "status": "review",
                    "error_code": "RCV_IDENTITY_MISMATCH",
                    "diagnostic": (
                        "El receptor del XML o la factura no coincide con el RCV."
                    ),
                }
            )
            return self._open_mipyme_recovery(recovery)
        try:
            with self.env.cr.savepoint():
                move = recovery.move_id or recovery.action_import_to_odoo()
                if isinstance(move, dict):
                    return move
                self.env["l10n.cl.rcv.matcher"]._reconcile(self)
                if move.move_type != "out_invoice" or self.move_id != move:
                    raise UserError(
                        _("No fue posible vincular la factura al documento RCV.")
                    )
        except (UserError, ValidationError, ValueError) as error:
            recovery.sudo().write(
                {
                    "status": "import_error",
                    "error_code": "RCV_IMPORT_FAILED",
                    "diagnostic": str(error),
                }
            )
            return self._open_mipyme_recovery(recovery)
        return self._open_mipyme_move(move)

    @staticmethod
    def _open_mipyme_move(move):
        return {
            "type": "ir.actions.act_window",
            "name": "Factura recuperada desde MiPyme",
            "res_model": "account.move",
            "res_id": move.id,
            "view_mode": "form",
            "target": "current",
        }

    @staticmethod
    def _open_mipyme_recovery(recovery):
        return {
            "type": "ir.actions.act_window",
            "name": "Resultado de recuperación MiPyme",
            "res_model": "l10n_cl.dte.recovery",
            "res_id": recovery.id,
            "view_mode": "form",
            "target": "current",
        }
