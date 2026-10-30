# Copyright 2026 Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare


class PosOrder(models.Model):
    _inherit = "pos.order"

    invoice_type = fields.Selection(
        [("boleta", "Boleta"), ("factura", "Factura")],
        string="Documento tributario POS",
        default="boleta",
        copy=False,
        readonly=True,
    )
    voucher_number = fields.Char(string="Número de voucher", copy=False, readonly=True)
    l10n_cl_dte_status = fields.Selection(
        related="account_move.l10n_cl_dte_status",
        string="Estado SII",
        readonly=True,
    )
    l10n_cl_dte_track_id = fields.Char(
        related="account_move.l10n_cl_dte_track_id",
        string="TrackID SII",
        readonly=True,
    )
    l10n_cl_dte_folio = fields.Integer(
        related="account_move.l10n_cl_dte_folio",
        string="Folio DTE",
        readonly=True,
    )
    l10n_cl_dte_type_code = fields.Char(
        related="account_move.l10n_cl_dte_type_code",
        string="Tipo DTE",
        readonly=True,
    )
    l10n_cl_dte_is_external = fields.Boolean(
        related="account_move.l10n_cl_dte_is_external",
        string="DTE externo",
        readonly=True,
    )
    l10n_cl_dte_pos_ted_image = fields.Char(
        compute="_compute_l10n_cl_dte_pos_ted_image",
        string="TED del DTE POS",
        compute_sudo=True,
    )
    l10n_cl_dte_document_type_name = fields.Char(
        related="account_move.l10n_latam_document_type_id.name",
        string="Tipo de documento DTE",
        readonly=True,
    )
    l10n_cl_dte_document_number = fields.Char(
        related="account_move.l10n_latam_document_number",
        string="Folio DTE emitido",
        readonly=True,
    )

    @api.depends("account_move.l10n_cl_dte_pos_ted_image")
    def _compute_l10n_cl_dte_pos_ted_image(self):
        for order in self:
            order.l10n_cl_dte_pos_ted_image = (
                order.account_move.sudo().l10n_cl_dte_pos_ted_image
                if order.account_move
                else False
            )

    def write(self, values):
        identity_fields = {"invoice_type", "voucher_number"} & values.keys()
        if identity_fields:
            for order in self.filtered(
                lambda record: (
                    record.company_id.account_fiscal_country_id.code == "CL"
                    and record.account_move
                )
            ):
                for field_name in identity_fields:
                    if values[field_name] != order[field_name]:
                        raise UserError(
                            _(
                                "No se puede cambiar %(field)s del pedido POS "
                                "porque ya tiene un DTE vinculado.",
                                field=order._fields[field_name].string,
                            )
                        )
        return super().write(values)

    @api.model
    def _process_order(self, order, existing_order):
        """Force Chilean POS sales through the DTE invoice lifecycle on sync."""
        order = dict(order)
        session = self.env["pos.session"].browse(order.get("session_id")).exists()
        if not session or session.company_id.account_fiscal_country_id.code != "CL":
            return super()._process_order(order, existing_order)

        document_kind = (
            order.get("invoice_type")
            or (existing_order.invoice_type if existing_order else False)
            or "boleta"
        )
        if document_kind not in ("boleta", "factura"):
            raise ValidationError(_("Seleccione boleta o factura para el pedido POS."))
        order["invoice_type"] = document_kind
        order["to_invoice"] = True
        if existing_order and existing_order.account_move:
            if not order.get("voucher_number"):
                order["voucher_number"] = existing_order.voucher_number

        if not order.get("partner_id"):
            order["partner_id"] = self.env.ref("l10n_cl.par_cfa").id
        anonymous_partner = self.env.ref("l10n_cl.par_cfa")
        if (
            document_kind == "factura"
            and int(order["partner_id"]) == anonymous_partner.id
        ):
            raise ValidationError(
                _("Seleccione un cliente con RUT para emitir una factura desde POS.")
            )
        if document_kind == "factura":
            partner = self.env["res.partner"].browse(order["partner_id"]).exists()
            self._l10n_cl_validate_pos_invoice_partner(partner)
        return super()._process_order(order, existing_order)

    @api.model
    def _l10n_cl_validate_pos_invoice_partner(self, partner):
        required_fields = {
            "l10n_cl_dte_email": _("correo DTE"),
            "l10n_cl_activity_description": _("giro"),
            "street": _("dirección"),
            "l10n_latam_identification_type_id": _("tipo de identificación"),
            "l10n_cl_sii_taxpayer_type": _("tipo de contribuyente"),
            "vat": _("RUT"),
        }
        missing = [
            label
            for field_name, label in required_fields.items()
            if not partner or not partner[field_name]
        ]
        if missing:
            raise ValidationError(
                _(
                    "Complete los datos requeridos del cliente: %(fields)s",
                    fields=", ".join(missing),
                )
            )

    def _prepare_invoice_vals(self):
        values = super()._prepare_invoice_vals()
        self.ensure_one()
        if self.company_id.account_fiscal_country_id.code != "CL":
            return values

        document_code = self._l10n_cl_pos_document_code(values["move_type"])
        document_type = self.env["l10n_latam.document.type"].search(
            [("country_id.code", "=", "CL"), ("code", "=", document_code)],
            limit=1,
        )
        if not document_type:
            raise UserError(
                _(
                    "No está configurado el tipo de documento DTE %(code)s.",
                    code=document_code,
                )
            )
        values["l10n_latam_document_type_id"] = document_type.id
        return values

    def _l10n_cl_pos_document_code(self, move_type):
        self.ensure_one()
        if move_type == "out_refund":
            if not self.refunded_order_id or not self.refunded_order_id.account_move:
                raise UserError(
                    _("Una devolución POS requiere un pedido original con factura DTE.")
                )
            return "61"
        exempt = self.currency_id.is_zero(self.amount_tax)
        if self.invoice_type == "factura":
            return "34" if exempt else "33"
        if self.invoice_type != "boleta":
            raise ValidationError(_("El pedido POS debe seleccionar boleta o factura."))
        return "41" if exempt else "39"

    def _create_invoice(self, move_vals):
        move = super()._create_invoice(move_vals)
        if (
            self.company_id.account_fiscal_country_id.code == "CL"
            and move.move_type == "out_refund"
        ):
            self._l10n_cl_create_pos_credit_note_reference(move)
        return move

    def _l10n_cl_create_pos_credit_note_reference(self, credit_note):
        self.ensure_one()
        source_order = self.refunded_order_id
        source_move = credit_note.reversed_entry_id or source_order.account_move
        if not source_move:
            raise UserError(_("No se encontró el DTE original de la devolución POS."))
        if source_move.l10n_cl_dte_status == "rejected":
            raise UserError(
                _(
                    "El DTE original fue rechazado. Corrija y vuelva a emitir "
                    "el pedido "
                    "original en lugar de crear una nota de crédito."
                )
            )
        source_code = source_move.l10n_latam_document_type_id.code
        if source_code not in ("33", "34", "39", "41"):
            raise UserError(
                _(
                    "El DTE original no es una factura o boleta POS que admita "
                    "devolución."
                )
            )
        if not source_move.l10n_latam_document_number:
            raise UserError(_("El DTE original aún no tiene folio para referenciar."))

        # CodRef=1 annuls the referenced DTE in full.  Cumulative POS return
        # quantities alone are insufficient: the last partial refund may
        # complete all returned quantities while covering only part of the
        # amount.  The current credit note amount alone is also insufficient
        # for zero-value or offsetting lines.  Require both signals.
        full_quantity_return = (
            self._l10n_cl_pos_refund_covers_all_source_quantities(source_order)
        )
        reference_code = self._l10n_cl_pos_credit_note_reference_code(
            credit_note, source_move, full_quantity_return
        )
        credit_note.write(
            {
                "l10n_cl_reference_ids": [
                    (
                        0,
                        0,
                        {
                            "origin_doc_number": source_move.l10n_latam_document_number,
                            "l10n_cl_reference_doc_type_id": (
                                source_move.l10n_latam_document_type_id.id
                            ),
                            "reference_doc_code": reference_code,
                            "reason": _(
                                "Devolución POS del documento %(document)s",
                                document=source_move.l10n_latam_document_number,
                            ),
                            "date": source_move.invoice_date,
                        },
                    )
                ]
            }
        )

    @api.model
    def _l10n_cl_pos_credit_note_reference_code(
        self, credit_note, source_move, full_quantity_return
    ):
        is_full_document_amount = (
            source_move.currency_id.compare_amounts(
                credit_note.amount_total, source_move.amount_total
            )
            == 0
        )
        return "1" if is_full_document_amount and full_quantity_return else "3"

    def _l10n_cl_pos_refund_covers_all_source_quantities(self, source_order):
        source_lines = source_order.lines.filtered(lambda line: line.qty > 0)
        if not source_lines:
            return False
        for source_line in source_lines:
            current_refund_lines = source_line.refund_orderline_ids.filtered(
                lambda line: line.order_id == self and line.order_id.state != "cancel"
            )
            current_refund_qty = -sum(current_refund_lines.mapped("qty"))
            if (
                float_compare(
                    current_refund_qty,
                    source_line.qty,
                    precision_rounding=source_line.product_uom_id.rounding,
                )
                != 0
            ):
                return False
        return True

    def action_l10n_cl_dte_open_invoice(self):
        self.ensure_one()
        if not self.account_move:
            raise UserError(_("El pedido POS todavía no tiene una factura DTE."))
        self.account_move.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Documento tributario POS"),
            "res_model": "account.move",
            "res_id": self.account_move.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_l10n_cl_dte_send(self):
        self.ensure_one()
        if not self.account_move:
            raise UserError(_("El pedido POS todavía no tiene una factura DTE."))
        return self.account_move.action_l10n_cl_dte_send()

    def action_l10n_cl_dte_check_status(self):
        self.ensure_one()
        if not self.account_move:
            raise UserError(_("El pedido POS todavía no tiene una factura DTE."))
        return self.account_move.action_l10n_cl_dte_check_status()

    def action_l10n_cl_dte_reconcile_uncertain(self):
        self.ensure_one()
        if not self.account_move:
            raise UserError(_("El pedido POS todavía no tiene una factura DTE."))
        self.account_move.check_access("read")
        return self.account_move.action_l10n_cl_dte_reconcile_uncertain()
