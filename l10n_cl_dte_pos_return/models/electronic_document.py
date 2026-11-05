# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from ..services.binding_context import is_native_binding
from ..services.pos_return_policy import PosReturnPolicyService


class ElectronicDocument(models.Model):
    _inherit = "l10n_cl.electronic.document"

    note_pos_return_order_id = fields.Many2one(
        "pos.order",
        string="POS Return",
        readonly=True,
        copy=False,
        check_company=True,
        index=True,
        ondelete="restrict",
    )
    pos_line_mapping_artifact_id = fields.Many2one(
        "l10n_cl.electronic.artifact",
        string="POS Line Mapping Evidence",
        readonly=True,
        copy=False,
        check_company=True,
        ondelete="restrict",
    )

    _sql_constraints = [
        (
            "note_pos_return_order_unique",
            "UNIQUE(note_pos_return_order_id)",
            "A POS return can issue only one electronic credit note.",
        ),
        (
            "pos_line_mapping_artifact_unique",
            "UNIQUE(pos_line_mapping_artifact_id)",
            "A POS line mapping belongs to one fiscal document.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("pos_line_mapping_artifact_id"):
                raise AccessError(
                    _(
                        "Assign fiscal mapping evidence through its validated "
                        "private setter."
                    )
                )
            if vals.get("note_pos_return_order_id"):
                order = (
                    self.env["pos.order"]
                    .browse(vals["note_pos_return_order_id"])
                    .exists()
                )
                if len(order) != 1:
                    raise ValidationError(_("The POS credit source does not exist."))
                PosReturnPolicyService().validate_native_result(order)
                if (
                    str(vals.get("document_code")) != "61"
                    or vals.get("note_target_document_id")
                    != order.dte_return_target_document_id.id
                ):
                    raise ValidationError(
                        _("The POS credit must reference its verified original boleta.")
                    )
        return super().create(vals_list)

    def write(self, vals):
        if "note_pos_return_order_id" in vals:
            raise AccessError(_("The fiscal POS return provenance is immutable."))
        if "pos_line_mapping_artifact_id" in vals and not is_native_binding(self.env):
            raise AccessError(_("The fiscal POS mapping evidence is immutable."))
        return super().write(vals)

    @api.model
    def _validate_note_source_values(self, vals, target, root):
        if vals.get("source_pos_order_id"):
            order = self.env["pos.order"].browse(vals["source_pos_order_id"]).exists()
            if (
                len(order) != 1
                or str(vals.get("document_code")) != "61"
                or vals.get("source_move_id")
                or vals.get("note_pos_return_order_id") != order.id
                or target != order.dte_return_target_document_id
                or root != target
                or order.dte_return_accounting_mode != "session_summary"
            ):
                raise ValidationError(
                    _(
                        "A direct POS credit requires an exact summarized native "
                        "return source."
                    )
                )
            PosReturnPolicyService().validate_native_result(order)
            return
        return super()._validate_note_source_values(vals, target, root)

    def _check_note_chain_operator(self, target_document, source=None):
        if self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            return super()._check_note_chain_operator(target_document, source=source)
        if not (
            self.env.user.has_group("point_of_sale.group_pos_user")
            and self.env.user.has_group("l10n_cl_dte_core.group_dte_user")
        ):
            return super()._check_note_chain_operator(target_document, source=source)
        source = source or self.note_pos_return_order_id
        order = (
            source.dte_pos_return_order_id
            if source and source._name == "account.move"
            else source
        )
        if (
            not order
            or order._name != "pos.order"
            or order.company_id != target_document.company_id
        ):
            raise AccessError(
                _("POS operators may serialize only verified POS fiscal sources.")
            )
        is_return = order.dte_return_target_document_id == target_document
        is_original = (
            order.amount_total > 0 and order._get_pos_dte_documents() == target_document
        )
        if (
            not (is_return or is_original)
            or target_document.company_id not in self.env.companies
        ):
            raise AccessError(
                _("The POS fiscal source does not match the adjustment root.")
            )
        target_document.check_access("read")
        order.check_access("read")

    def _set_pos_line_mapping_artifact(self, artifact):
        self.ensure_one()
        if (
            not is_native_binding(self.env)
            or self.pos_line_mapping_artifact_id
            or artifact.document_id != self
            or artifact.company_id != self.company_id
            or artifact.role != "pos_line_mapping"
        ):
            raise ValidationError(
                _(
                    "Fiscal POS mapping evidence can be assigned only once "
                    "to its own document."
                )
            )
        artifact.sudo().action_verify_integrity()
        self.sudo().write({"pos_line_mapping_artifact_id": artifact.id})

    @api.constrains("note_pos_return_order_id", "source_pos_order_id", "source_move_id")
    def _check_pos_return_source(self):
        for document in self.filtered("note_pos_return_order_id"):
            order = document.note_pos_return_order_id
            if (
                document.company_id != order.company_id
                or document.document_code != "61"
            ):
                raise ValidationError(
                    _("The POS return and fiscal credit identity differ.")
                )
            if order.dte_return_accounting_mode == "session_summary":
                valid = (
                    document.source_pos_order_id == order
                    and not document.source_move_id
                )
            else:
                valid = (
                    document.source_move_id == order.account_move
                    and not document.source_pos_order_id
                )
            if not valid:
                raise ValidationError(
                    _(
                        "The POS credit must have exactly one authoritative "
                        "fiscal source."
                    )
                )
