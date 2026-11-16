# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

EXPORT_CODES = {"110", "111", "112"}


class AccountMove(models.Model):
    _inherit = "account.move"

    dte_export_service = fields.Selection(
        [
            ("goods", "Goods"),
            ("3", "Customs-qualified Services"),
            ("4", "Hotel Services"),
            ("5", "International Land Transport"),
        ],
    )
    dte_export_receiver_id = fields.Char(string="Foreign Recipient ID", size=20)
    dte_export_receiver_extra_id = fields.Char(
        string="Additional Recipient ID", size=20
    )
    dte_export_nationality_id = fields.Many2one(
        "res.country", string="Recipient Nationality", ondelete="restrict"
    )
    dte_export_destination_id = fields.Many2one(
        "res.country", string="Export Destination", ondelete="restrict"
    )
    dte_export_rate = fields.Float(
        string="CLP per Transaction Currency Unit", digits=(10, 4), copy=False
    )
    dte_export_rate_date = fields.Date(string="Export Exchange Rate Date", copy=False)
    dte_export_rate_source = fields.Char(
        string="Export Exchange Rate Source", copy=False
    )

    def _check_export_access(self, operation):
        self.ensure_one()
        self.check_access("read")
        self.check_access(operation)
        if not self.env.su and (
            self.company_id not in self.env.companies
            or self.company_id not in self.env.user.company_ids
        ):
            raise AccessError(_("The export source company must be allowed."))
        group = (
            "group_dte_manager"
            if self.dte_document_type_code in {"111", "112"}
            else "group_dte_user"
        )
        if not self.env.user.has_group(f"l10n_cl_dte_core.{group}"):
            raise AccessError(
                _("Export inputs require a DTE user, or a manager for notes.")
            )

    def _check_dte_source_access(self, document_code):
        result = super()._check_dte_source_access(document_code)
        if (
            str(document_code) in EXPORT_CODES
            or self.dte_document_type_code in EXPORT_CODES
        ):
            self._check_export_access("write")
            if str(document_code) != self.dte_document_type_code:
                raise UserError(_("The requested export type must match the source."))
            self._get_dte_source_documents()
        return result

    def _check_export_input_mutation(self, operation):
        if not self:
            return
        for move in self:
            move._check_export_access(operation)
        self.flush_recordset(["state", "company_id", "l10n_latam_document_type_id"])
        self.env.cr.execute(
            "SELECT id FROM account_move WHERE id IN %s ORDER BY id FOR UPDATE",
            [tuple(self.ids)],
        )
        self.invalidate_recordset(
            ["state", "company_id", "l10n_latam_document_type_id"]
        )
        for move in self:
            move._check_export_access(operation)
            if move.state != "draft":
                raise UserError(_("Export inputs can only change on a draft source."))
        if self._get_dte_source_documents():
            raise UserError(_("Export inputs cannot change after DTE evidence exists."))

    @api.model_create_multi
    def create(self, vals_list):
        inputs = [name for name in self._fields if name.startswith("dte_export_")]
        with self.env.cr.savepoint():
            moves = super().create(vals_list)
            moves.filtered(
                lambda move: any(move[name] for name in inputs)
            )._check_export_input_mutation("create")
        return moves

    def write(self, vals):
        inputs = {name for name in self._fields if name.startswith("dte_export_")}
        guarded = bool(inputs.intersection(vals))
        if not guarded and {"company_id", "l10n_latam_document_type_id"}.intersection(
            vals
        ):
            guarded = any(any(move[name] for name in inputs) for move in self)
        if not guarded:
            return super().write(vals)
        with self.env.cr.savepoint():
            self._check_export_input_mutation("write")
            result = super().write(vals)
            self._check_export_input_mutation("write")
        return result
