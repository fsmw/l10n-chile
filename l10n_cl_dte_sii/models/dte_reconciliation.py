# Copyright 2026 Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_RECONCILIATION_CREATE_TOKEN = object()


class L10nClDteReconciliation(models.Model):
    _name = "l10n_cl.dte.reconciliation"
    _description = "Conciliación manual de envío DTE incierto"
    _order = "resolved_at desc, id desc"
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True)
    move_id = fields.Many2one(
        "account.move", required=True, readonly=True, check_company=True, index=True
    )
    company_id = fields.Many2one(
        related="move_id.company_id", store=True, readonly=True, index=True
    )
    resolution = fields.Selection(
        [
            ("received", "Recepción confirmada"),
            ("not_received", "No recibido; reenvío autorizado"),
        ],
        required=True,
        readonly=True,
    )
    track_id = fields.Char(readonly=True)
    previous_track_id = fields.Char(readonly=True)
    previous_diagnostic = fields.Text(readonly=True)
    evidence = fields.Text(required=True, readonly=True)
    resolved_by = fields.Many2one("res.users", required=True, readonly=True)
    resolved_at = fields.Datetime(required=True, readonly=True)

    @api.depends("move_id", "resolution", "resolved_at")
    def _compute_name(self):
        labels = dict(self._fields["resolution"]._description_selection(self.env))
        for record in self:
            record.name = _("%(resolution)s · %(move)s · %(date)s") % {
                "resolution": labels.get(record.resolution, ""),
                "move": record.move_id.display_name or "",
                "date": record.resolved_at or "",
            }

    @api.model_create_multi
    def create(self, vals_list):
        if (
            self.env.context.get("_dte_reconciliation_create_token")
            is not _RECONCILIATION_CREATE_TOKEN
        ):
            raise AccessError(
                _("La conciliación debe registrarse desde el asistente autorizado.")
            )
        return super().create(vals_list)

    def write(self, values):
        raise AccessError(_("La evidencia de conciliación DTE es inmutable."))

    def unlink(self):
        raise AccessError(_("La evidencia de conciliación DTE no se puede eliminar."))

    @api.model
    def resolve_uncertain_submission(self, move, resolution, track_id, evidence):
        if not self.env.user.has_group("l10n_cl_dte_sii.group_dte_manager"):
            raise AccessError(
                _("Sólo un responsable DTE puede conciliar un envío incierto.")
            )
        move.ensure_one()
        move.check_access("read")
        move._check_dte_action_access()
        move._lock_dte_lifecycle()
        previous_track_id = str(move.l10n_cl_dte_track_id or "").strip()
        malformed_track_id = bool(
            previous_track_id and not re.fullmatch(r"\d{10}", previous_track_id)
        )
        if (
            move.state != "posted"
            or move.l10n_cl_dte_type_code not in ("33", "34")
            or move.l10n_cl_dte_is_external
            or move.l10n_cl_dte_status != "error"
            or (
                previous_track_id
                and not (resolution == "received" and malformed_track_id)
            )
        ):
            raise UserError(
                _(
                    "Sólo se puede conciliar una factura 33/34 contabilizada, "
                    "con envío incierto sin TrackID, o con recepción confirmada "
                    "para reemplazar un TrackID malformado."
                )
            )
        evidence = str(evidence or "").strip()
        if not evidence:
            raise ValidationError(
                _("Indique la evidencia consultada para resolver el envío incierto.")
            )
        track_id = str(track_id or "").strip()
        if resolution == "received":
            if not re.fullmatch(r"\d{10}", track_id):
                raise ValidationError(
                    _("La recepción confirmada requiere un TrackID SII de 10 dígitos.")
                )
            move_values = {
                "l10n_cl_dte_status": "ask_for_status",
                "l10n_cl_dte_track_id": track_id,
                "l10n_cl_dte_diagnostic": _(
                    "Recepción conciliada manualmente; consulte el TrackID SII."
                ),
            }
        elif resolution == "not_received":
            if track_id:
                raise ValidationError(
                    _("Un envío conciliado como no recibido no puede tener TrackID.")
                )
            move_values = {
                "l10n_cl_dte_status": "not_sent",
                "l10n_cl_dte_diagnostic": _(
                    "No recepción confirmada manualmente; reenvío autorizado."
                ),
            }
        else:
            raise ValidationError(_("Seleccione una resolución válida."))

        reconciliation = (
            self.sudo()
            .with_context(_dte_reconciliation_create_token=_RECONCILIATION_CREATE_TOKEN)
            .create(
                {
                    "move_id": move.id,
                    "resolution": resolution,
                    "track_id": track_id or False,
                    "previous_track_id": previous_track_id or False,
                    "previous_diagnostic": move.l10n_cl_dte_diagnostic,
                    "evidence": evidence,
                    "resolved_by": self.env.user.id,
                    "resolved_at": fields.Datetime.now(),
                }
            )
        )
        move.sudo()._l10n_cl_write_dte_lifecycle_values(move_values)
        return reconciliation
