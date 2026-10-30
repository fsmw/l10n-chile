# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from datetime import datetime

import pytz
from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..services.dte_xml import DteXmlBuilder, serialize_xml
from ..services.incoming_dte_xml import validate_dte_element
from ..services.sii_client import SiiDteClient, SiiTransportError

OUTBOUND_DOCUMENT_CODES = {"33", "34", "39", "41", "56", "61"}
# Receiver data the SII does not require for these documents.
_DTE_NO_RECEIVER_ACTIVITY_CODES = frozenset({"39", "41", "110", "111", "112"})
# Document types whose totals must not include taxes.
_DTE_EXEMPT_DOCUMENT_CODES = frozenset({"34", "41", "110", "111", "112"})
# States in which the SII already owns the DTE lifecycle: the local journal
# entry cannot be cancelled or reset to draft, only reversed.
_DTE_BLOCKED_STATES = frozenset({"accepted", "objected", "ask_for_status"})
_DTE_LIFECYCLE_LOCK_NAMESPACE = 0x4C43444C
_DTE_LIFECYCLE_FIELDS = frozenset(
    {
        "l10n_cl_dte_status",
        "l10n_cl_dte_track_id",
        "l10n_cl_dte_folio",
        "l10n_cl_dte_caf_id",
        "l10n_cl_dte_attachment_id",
        "l10n_cl_dte_envelope_attachment_id",
        "l10n_cl_dte_file",
        "l10n_cl_sii_barcode",
        "l10n_cl_dte_receiver_status",
        "l10n_cl_dte_last_status_check",
        "l10n_cl_dte_diagnostic",
        "l10n_cl_dte_last_status_error",
        "l10n_cl_dte_sii_status_code",
        "l10n_cl_dte_sii_server_code",
        "l10n_cl_dte_sii_sql_code",
        "l10n_cl_dte_sii_error_code",
        "l10n_cl_dte_sii_token_code",
        "l10n_cl_dte_sii_attention_number",
        "l10n_cl_dte_sii_document_type",
        "l10n_cl_dte_sii_has_summary",
        "l10n_cl_dte_sii_informed_count",
        "l10n_cl_dte_sii_accepted_count",
        "l10n_cl_dte_sii_rejected_count",
        "l10n_cl_dte_sii_objection_count",
    }
)


class AccountMove(models.Model):
    _inherit = "account.move"

    @api.model_create_multi
    def create(self, vals_list):
        if any(_DTE_LIFECYCLE_FIELDS.intersection(vals) for vals in vals_list):
            raise UserError(
                _(
                    "Los datos fiscales del DTE no pueden establecerse al crear "
                    "una factura; use el flujo de emisión o importación autorizado."
                )
            )
        return super().create(vals_list)

    def write(self, vals):
        if _DTE_LIFECYCLE_FIELDS.intersection(vals):
            raise UserError(
                _(
                    "Los datos fiscales del DTE sólo se actualizan mediante los "
                    "flujos autorizados de emisión, consulta, conciliación o "
                    "importación."
                )
            )
        return super().write(vals)

    def _l10n_cl_write_dte_lifecycle_values(self, vals):
        """Write protocol results from trusted server-side lifecycle handlers."""
        if not _DTE_LIFECYCLE_FIELDS.intersection(vals):
            raise ValidationError(_("No se indicaron campos del ciclo de vida DTE."))
        return super().write(vals)

    def _l10n_cl_check_can_reverse_dte(self):
        rejected_dte = self.filtered(
            lambda move: (
                move.company_id.account_fiscal_country_id.code == "CL"
                and move.l10n_cl_dte_status == "rejected"
            )
        )
        if rejected_dte:
            raise UserError(
                _(
                    "El DTE fue rechazado por el SII. Corrija y vuelva a emitir el "
                    "documento original en lugar de crear una reversa."
                )
            )

    def _l10n_cl_dte_state_guard_moves(self):
        """Return the moves whose DTE lifecycle is owned by the SII."""
        return self.filtered(
            lambda move: (
                move.company_id.account_fiscal_country_id.code == "CL"
                and move.l10n_latam_document_type_id
            )
        )

    def _l10n_cl_check_dte_state(self, action):
        """Refuse to cancel or reset a DTE already processed by the SII.

        ``action`` is ``"cancel"`` or ``"draft"``. Both share the same blocked
        states but report a different message. Moves without a fiscal document
        type, and companies outside Chile, keep the standard behaviour.
        """
        if action not in ("cancel", "draft"):
            raise ValueError(f"Unsupported DTE state guard action: {action}")
        blocked = self._l10n_cl_dte_state_guard_moves().filtered(
            lambda move: move.l10n_cl_dte_status in _DTE_BLOCKED_STATES
        )
        if not blocked:
            return
        move = blocked[0]
        status = move.l10n_cl_dte_status
        document_type = move.l10n_latam_document_type_id.display_name
        if status == "ask_for_status":
            if action == "cancel":
                raise UserError(
                    _(
                        "El DTE %(document_type)s está en consulta de estado en "
                        "el SII. No se puede cancelar hasta que el SII lo "
                        "rechace; si resulta aceptado o aceptado con reparos, "
                        "debe revertirse con un documento de reversa.",
                        document_type=document_type,
                    )
                )
            raise UserError(
                _(
                    "El DTE %(document_type)s está en consulta de estado en el "
                    "SII. No se puede devolver a borrador hasta que el SII lo "
                    "rechace; si resulta aceptado o aceptado con reparos, debe "
                    "revertirse con un documento de reversa.",
                    document_type=document_type,
                )
            )
        status_label = dict(
            move._fields["l10n_cl_dte_status"]._description_selection(self.env)
        ).get(status, status)
        if action == "cancel":
            raise UserError(
                _(
                    "El DTE %(document_type)s está en estado SII "
                    "«%(status)s». No se puede cancelar: el documento ya fue "
                    "procesado por el SII y debe revertirse con un documento de "
                    "reversa.",
                    document_type=document_type,
                    status=status_label,
                )
            )
        raise UserError(
            _(
                "El DTE %(document_type)s está en estado SII «%(status)s». No "
                "se puede devolver a borrador: el documento ya fue procesado "
                "por el SII y debe revertirse con un documento de reversa.",
                document_type=document_type,
                status=status_label,
            )
        )

    def button_cancel(self):
        self._l10n_cl_check_dte_state("cancel")
        result = super().button_cancel()
        moves = self._l10n_cl_dte_state_guard_moves()
        if moves:
            moves._l10n_cl_write_dte_lifecycle_values(
                {"l10n_cl_dte_status": "cancelled"}
            )
        return result

    def button_draft(self):
        self._l10n_cl_check_dte_state("draft")
        result = super().button_draft()
        moves = self._l10n_cl_dte_state_guard_moves()
        if moves:
            moves._l10n_cl_write_dte_lifecycle_values({"l10n_cl_dte_status": False})
        return result

    def _l10n_cl_normalize_currency_name(self, currency_name):
        """Provide the currency label expected by Odoo's Chilean DTE totals."""
        currency_names = {
            "AED": "DIRHAM",
            "ARS": "PESO",
            "AUD": "DOLAR AUST",
            "BOB": "BOLIVIANO",
            "BRL": "CRUZEIRO REAL",
            "CAD": "DOLAR CAN",
            "CHF": "FRANCO SZ",
            "CLP": "PESO CL",
            "CNY": "RENMINBI",
            "COP": "PESO COL",
            "ECS": "SUCRE",
            "EUR": "EURO",
            "GBP": "LIBRA EST",
            "HKD": "DOLAR HK",
            "INR": "RUPIA",
            "JPY": "YEN",
            "MXN": "PESO MEX",
            "NOK": "CORONA NOR",
            "NZD": "DOLAR NZ",
            "PEN": "NUEVO SOL",
            "PYG": "GUARANI",
            "SEK": "CORONA SC",
            "SGD": "DOLAR SIN",
            "TWD": "DOLAR TAI",
            "USD": "DOLAR USA",
            "UYU": "PESO URUG",
            "VEF": "BOLIVAR",
            "ZAR": "RAND",
        }
        return currency_names.get(currency_name, "OTRAS MONEDAS")

    @api.model
    def _format_length(self, text, text_len):
        """Keep the helper contract used by Chilean invoice line totals."""
        return text and text[:text_len] or ""

    l10n_cl_dte_status = fields.Selection(
        [
            ("not_sent", "Pendiente de envío"),
            ("ask_for_status", "En consulta"),
            ("accepted", "Aceptado"),
            ("objected", "Aceptado con reparos"),
            ("rejected", "Rechazado"),
            ("cancelled", "Cancelado en el sistema anterior"),
            ("error", "Error"),
            ("manual", "Manual"),
        ],
        copy=False,
        readonly=True,
        tracking=True,
        index=True,
    )
    # Kept for historical documents migrated from the legacy emitter. New
    # documents render TED from the signed XML attachment.
    l10n_cl_sii_barcode = fields.Char(
        string="TED histórico",
        copy=False,
        readonly=True,
        groups="base.group_system",
    )
    l10n_cl_dte_track_id = fields.Char(copy=False, readonly=True, tracking=True)
    l10n_cl_dte_type_code = fields.Char(
        compute="_compute_l10n_cl_dte_type_code", store=True
    )
    l10n_cl_dte_receiver_status = fields.Selection(
        [("not_sent", "No enviado"), ("sent", "Enviado")],
        string="Estado del receptor",
        copy=False,
        readonly=True,
    )
    l10n_cl_dte_receiver_delivery_ids = fields.One2many(
        "l10n_cl.dte.receiver.delivery", "move_id", readonly=True
    )
    l10n_cl_dte_receiver_delivery_active = fields.Boolean(
        compute="_compute_l10n_cl_dte_receiver_delivery_active"
    )
    l10n_cl_dte_receiver_delivery_status = fields.Selection(
        [
            ("not_sent", "No enviado"),
            ("outgoing", "En cola"),
            ("sent", "Enviado"),
            ("exception", "Error de entrega"),
            ("cancel", "Cancelado"),
            ("legacy_sent", "Envío histórico registrado"),
        ],
        compute="_compute_l10n_cl_dte_receiver_delivery_active",
    )
    l10n_cl_dte_last_status_check = fields.Datetime(
        string="Última consulta SII", copy=False, readonly=True
    )
    l10n_cl_dte_diagnostic = fields.Text(
        string="Diagnóstico SII", copy=False, readonly=True
    )
    l10n_cl_dte_last_status_error = fields.Text(
        string="Error de la última consulta SII", copy=False, readonly=True
    )
    l10n_cl_dte_sii_status_code = fields.Char(
        string="Código de estado SII", copy=False, readonly=True
    )
    l10n_cl_dte_sii_server_code = fields.Char(
        string="Código de servicio SII", copy=False, readonly=True
    )
    l10n_cl_dte_sii_sql_code = fields.Char(
        string="Código SQL SII", copy=False, readonly=True
    )
    l10n_cl_dte_sii_error_code = fields.Char(
        string="Código de error SII", copy=False, readonly=True
    )
    l10n_cl_dte_sii_token_code = fields.Char(
        string="Código de token SII", copy=False, readonly=True
    )
    l10n_cl_dte_sii_attention_number = fields.Char(
        string="Número de atención SII", copy=False, readonly=True
    )
    l10n_cl_dte_sii_document_type = fields.Char(
        string="Tipo de documento informado", copy=False, readonly=True
    )
    l10n_cl_dte_sii_has_summary = fields.Boolean(
        string="El SII informó resumen del envío", copy=False, readonly=True
    )
    l10n_cl_dte_sii_informed_count = fields.Integer(
        string="Documentos informados", copy=False, readonly=True
    )
    l10n_cl_dte_sii_accepted_count = fields.Integer(
        string="Documentos aceptados", copy=False, readonly=True
    )
    l10n_cl_dte_sii_rejected_count = fields.Integer(
        string="Documentos rechazados", copy=False, readonly=True
    )
    l10n_cl_dte_sii_objection_count = fields.Integer(
        string="Documentos con reparos", copy=False, readonly=True
    )
    l10n_cl_dte_reconciliation_ids = fields.One2many(
        "l10n_cl.dte.reconciliation", "move_id", readonly=True
    )
    l10n_cl_dte_attachment_id = fields.Many2one(
        "ir.attachment", copy=False, readonly=True, groups="base.group_system"
    )
    # Backward-compatible field used by the optional MiPyme recovery addon.
    l10n_cl_dte_file = fields.Many2one(
        related="l10n_cl_dte_attachment_id",
        string="Archivo DTE",
        readonly=False,
        copy=False,
    )
    l10n_cl_dte_envelope_attachment_id = fields.Many2one(
        "ir.attachment", copy=False, readonly=True, groups="base.group_system"
    )
    l10n_cl_dte_is_external = fields.Boolean(compute="_compute_l10n_cl_dte_is_external")
    l10n_cl_dte_has_xml = fields.Boolean(
        compute="_compute_l10n_cl_dte_attachments", compute_sudo=True
    )
    l10n_cl_dte_has_envelope = fields.Boolean(
        compute="_compute_l10n_cl_dte_attachments", compute_sudo=True
    )
    l10n_cl_dte_folio = fields.Integer(copy=False, readonly=True, index=True)
    l10n_cl_dte_caf_id = fields.Many2one(
        "l10n_cl.dte.caf", copy=False, readonly=True, index=True
    )
    l10n_cl_reference_ids = fields.One2many(
        "l10n_cl.account.invoice.reference", "move_id", string="DTE References"
    )

    @api.depends("l10n_cl_dte_attachment_id", "l10n_cl_dte_envelope_attachment_id")
    def _compute_l10n_cl_dte_attachments(self):
        for move in self:
            move.l10n_cl_dte_has_xml = bool(move.l10n_cl_dte_attachment_id)
            move.l10n_cl_dte_has_envelope = bool(
                move.l10n_cl_dte_envelope_attachment_id
            )

    @api.depends(
        "l10n_cl_dte_receiver_delivery_ids.state", "l10n_cl_dte_receiver_status"
    )
    def _compute_l10n_cl_dte_receiver_delivery_active(self):
        for move in self:
            latest = move.l10n_cl_dte_receiver_delivery_ids.sorted("id", reverse=True)[
                :1
            ]
            if latest:
                state = latest.state
                move.l10n_cl_dte_receiver_delivery_status = state
                move.l10n_cl_dte_receiver_delivery_active = state in {
                    "outgoing",
                    "sent",
                }
            else:
                is_legacy_sent = move.l10n_cl_dte_receiver_status == "sent"
                move.l10n_cl_dte_receiver_delivery_status = (
                    "legacy_sent" if is_legacy_sent else "not_sent"
                )
                move.l10n_cl_dte_receiver_delivery_active = is_legacy_sent

    @api.depends("l10n_latam_document_type_id")
    def _compute_l10n_cl_dte_type_code(self):
        for move in self:
            move.l10n_cl_dte_type_code = move.l10n_latam_document_type_id.code or False

    def _compute_l10n_cl_dte_is_external(self):
        for move in self:
            move.l10n_cl_dte_is_external = bool(
                "l10n_cl_mipyme_imported" in move._fields
                and move.l10n_cl_mipyme_imported
            )

    def _l10n_cl_requires_dte_issue(self):
        self.ensure_one()
        return bool(
            self.state == "posted"
            and self.company_id.account_fiscal_country_id.code == "CL"
            and self.move_type in ("out_invoice", "out_refund")
            and self.l10n_latam_document_type_id.code
            in self._l10n_cl_dte_document_codes()
            and self.journal_id.l10n_cl_dte_point_of_sale == "online"
            and not self.l10n_cl_dte_is_external
        )

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        if self.env.context.get("l10n_cl_skip_dte_emission"):
            return posted
        for move in posted.filtered(
            lambda record: record._l10n_cl_requires_dte_issue()
        ):
            move._l10n_cl_prepare_dte()
        return posted

    def _reverse_moves(self, default_values_list=None, cancel=False):
        self._l10n_cl_check_can_reverse_dte()
        reverse_moves = super()._reverse_moves(
            default_values_list=default_values_list, cancel=cancel
        )
        if self.env.context.get("default_l10n_cl_edi_reference_doc_code") != "2":
            return reverse_moves
        for move in reverse_moves:
            move.invoice_line_ids = [
                (5, 0, 0),
                (
                    0,
                    0,
                    {
                        "account_id": move.journal_id.default_account_id.id,
                        "name": _(
                            "Donde dice: %(original)s debe decir: %(corrected)s",
                            original=self.env.context.get(
                                "default_l10n_cl_original_text"
                            ),
                            corrected=self.env.context.get(
                                "default_l10n_cl_corrected_text"
                            ),
                        ),
                        "quantity": 1,
                        "price_unit": 0.0,
                    },
                ),
            ]
        return reverse_moves

    def _set_next_sequence(self):
        """Use the locked CAF folio as the LATAM document sequence number."""
        self.ensure_one()
        if not self._l10n_cl_candidate_for_dte():
            return super()._set_next_sequence()
        caf = self.env["l10n_cl.dte.caf"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("document_type_id", "=", self.l10n_latam_document_type_id.id),
                ("state", "=", "in_use"),
            ],
            order="next_folio, id",
            limit=1,
        )
        if not caf:
            raise ValidationError(
                _("Upload and activate an SII CAF for this document type.")
            )
        folio = caf._reserve_next_folio()
        prefix = self.l10n_latam_document_type_id.doc_code_prefix
        if not prefix:
            raise ValidationError(
                _("The Chilean document type has no accounting sequence prefix.")
            )
        self._l10n_cl_write_dte_lifecycle_values(
            {"l10n_cl_dte_folio": folio, "l10n_cl_dte_caf_id": caf.id}
        )
        sequence = f"{prefix} {folio:06d}"
        self.with_context(clear_sequence_mixin_cache=False)[self._sequence_field] = (
            sequence
        )
        self._compute_split_sequence()

    def _l10n_cl_candidate_for_dte(self):
        self.ensure_one()
        return bool(
            self.company_id.account_fiscal_country_id.code == "CL"
            and self.move_type in ("out_invoice", "out_refund")
            and self.l10n_latam_document_type_id.code
            in self._l10n_cl_dte_document_codes()
            and self.journal_id.l10n_cl_dte_point_of_sale == "online"
            and not self.env.context.get("l10n_cl_skip_dte_emission")
            and not (
                "l10n_cl_mipyme_imported" in self._fields
                and self.l10n_cl_mipyme_imported
            )
        )

    @api.model
    def _l10n_cl_dte_document_codes(self):
        """Allow optional addons to extend issuance without replacing lifecycle."""
        return OUTBOUND_DOCUMENT_CODES

    def _l10n_cl_dte_xml_builder(self, folio, caf_root, certificate):
        self.ensure_one()
        return DteXmlBuilder(self, folio, caf_root, certificate)

    def _l10n_cl_edi_post_validation(self):
        """Validate issuer, receiver and document data before building the DTE."""
        self.ensure_one()
        self._l10n_cl_check_dte_emails()
        self._l10n_cl_check_stamp_date()
        self._l10n_cl_check_receiver_activity()
        self._l10n_cl_check_receiver_address()
        self._l10n_cl_check_electronic_ticket()
        self._l10n_cl_check_exempt_document()
        self._l10n_cl_check_taxed_document()

    def _l10n_cl_check_dte_emails(self):
        """Require the DTE mailbox of the issuer and of the receiver."""
        document_type = self.l10n_latam_document_type_id
        if (
            document_type._is_doc_type_export()
            or document_type._is_doc_type_electronic_ticket()
        ):
            return
        if not self.company_id.sudo().l10n_cl_dte_email:
            raise ValidationError(
                _(
                    "Falta el correo DTE del emisor. Configúrelo en la compañía "
                    "antes de emitir un DTE electrónico."
                )
            )
        if not (
            self.partner_id.l10n_cl_dte_email
            or self.commercial_partner_id.l10n_cl_dte_email
        ):
            raise ValidationError(
                _(
                    "Falta el correo DTE del receptor %(partner)s. Configúrelo "
                    "en el contacto antes de emitir un DTE electrónico.",
                    partner=self.partner_id.display_name,
                )
            )

    def _l10n_cl_check_stamp_date(self):
        """The stamp cannot be earlier than the invoice issue date."""
        if not self.invoice_date:
            return
        stamp_date = (
            datetime.now(pytz.UTC).astimezone(pytz.timezone("America/Santiago")).date()
        )
        if stamp_date < self.invoice_date:
            raise ValidationError(
                _(
                    "La fecha de timbre no puede ser anterior a la fecha de "
                    "emisión (%(invoice_date)s). Revise la fecha del documento "
                    "o la zona horaria America/Santiago.",
                    invoice_date=fields.Date.to_string(self.invoice_date),
                )
            )

    def _l10n_cl_check_receiver_activity(self):
        """Require the receiver's economic activity where the SII needs it."""
        if self.l10n_latam_document_type_id.code in _DTE_NO_RECEIVER_ACTIVITY_CODES:
            return
        if not (
            self.partner_id.l10n_cl_activity_description
            or self.commercial_partner_id.l10n_cl_activity_description
        ):
            raise ValidationError(
                _(
                    "Falta la actividad económica (giro) del receptor "
                    "%(partner)s. Es obligatoria para este tipo de documento.",
                    partner=self.partner_id.display_name,
                )
            )

    def _l10n_cl_check_receiver_address(self):
        """Require a street address unless the SII allows a ticket without it."""
        if self.l10n_latam_document_type_id._is_doc_type_electronic_ticket():
            return
        if not self.partner_id.street:
            raise ValidationError(
                _(
                    "Falta la dirección del receptor %(partner)s. Es obligatoria "
                    "para este tipo de documento.",
                    partner=self.partner_id.display_name,
                )
            )

    def _l10n_cl_check_electronic_ticket(self):
        """A boleta 39 may not carry ILA/withholdings and must use CLP."""
        if self.l10n_latam_document_type_id.code != "39":
            return
        if self._l10n_cl_get_withholdings():
            raise ValidationError(
                _("La boleta electrónica 39 no admite impuestos ILA ni retenciones.")
            )
        if self.currency_id != self.company_id.currency_id:
            raise ValidationError(
                _(
                    "La boleta electrónica 39 debe emitirse en la moneda de la "
                    "compañía (%(currency)s).",
                    currency=self.company_id.currency_id.name,
                )
            )

    def _l10n_cl_check_exempt_document(self):
        """An exempt document cannot carry taxes."""
        if self.l10n_latam_document_type_id.code not in _DTE_EXEMPT_DOCUMENT_CODES:
            return
        currency = self.currency_id
        if currency.compare_amounts(self.amount_untaxed, self.amount_total) != 0:
            raise ValidationError(
                _(
                    "El documento exento %(document_type)s tiene impuestos: el "
                    "total afecto debe ser igual al total. Revise las líneas o "
                    "use el tipo de documento afecto.",
                    document_type=self.l10n_latam_document_type_id.display_name,
                )
            )

    def _l10n_cl_check_taxed_document(self):
        """A factura 33 must have at least one VAT-taxed line."""
        if self.l10n_latam_document_type_id.code != "33":
            return
        currency = self.currency_id
        if currency.compare_amounts(self.amount_untaxed, self.amount_total) == 0:
            raise ValidationError(
                _(
                    "La factura 33 no tiene ítems afectos a IVA. Si el documento "
                    "es exento, use el tipo de documento 34."
                )
            )

    def _l10n_cl_prepare_dte(self):
        """Validate the reserved folio, build and persist signed DTE documents."""
        self.ensure_one()
        self._check_dte_action_access()
        self._l10n_cl_edi_post_validation()
        company = self.company_id.sudo()
        if company.l10n_cl_dte_environment not in ("SIIDEMO", "SIITEST", "SII"):
            raise ValidationError(
                _("Configure the SII environment on the company first.")
            )
        if (
            not self.journal_id.l10n_cl_dte_sucursal
            or not self.journal_id.l10n_cl_dte_office_number
        ):
            raise ValidationError(
                _("Configure the SII branch and office on the journal first.")
            )
        if (
            not company.l10n_cl_dte_resolution_number
            or not company.l10n_cl_dte_resolution_date
        ):
            raise ValidationError(
                _("Configure the SII resolution number and date before issuing DTEs.")
            )
        if not company.l10n_cl_dte_activity or not company.l10n_cl_dte_activity_code:
            raise ValidationError(
                _("Configure the issuer's economic activity and SII activity code.")
            )
        if not self.invoice_date:
            raise ValidationError(_("Set the invoice date before issuing a DTE."))
        certificate = company._get_digital_signature(self.env.user.id)
        caf = self.l10n_cl_dte_caf_id.sudo()
        folio = self.l10n_cl_dte_folio
        if (
            not caf
            or caf.company_id != company
            or caf.document_type_id != self.l10n_latam_document_type_id
            or not caf.start_nb <= folio <= caf.final_nb
        ):
            raise ValidationError(
                _("The accounting sequence has no matching authorized CAF folio.")
            )
        try:
            document_number = int(self.l10n_latam_document_number)
        except (TypeError, ValueError) as error:
            raise ValidationError(
                _("Odoo did not assign a numeric DTE folio to this document.")
            ) from error
        if document_number != folio:
            raise ValidationError(
                _(
                    "The accounting sequence %(sequence)s does not match reserved "
                    "CAF folio %(folio)s.",
                    sequence=self.l10n_latam_document_number,
                    folio=folio,
                )
            )
        caf_root = caf._decode_caf()
        builder = self._l10n_cl_dte_xml_builder(folio, caf_root, certificate)
        signed_dte = builder.build()
        signed_envelope = builder.build_envelope(signed_dte)
        attachment_model = self.env["ir.attachment"].sudo()
        xml_attachment = attachment_model.create(
            {
                "name": f"DTE_{builder.code}_{folio}.xml",
                "type": "binary",
                "datas": base64.b64encode(serialize_xml(signed_dte)),
                "mimetype": "application/xml",
                "res_model": self._name,
                "res_id": self.id,
                "company_id": company.id,
            }
        )
        envelope_attachment = attachment_model.create(
            {
                "name": f"ENVIO_{builder.code}_{folio}.xml",
                "type": "binary",
                "datas": base64.b64encode(serialize_xml(signed_envelope)),
                "mimetype": "application/xml",
                "res_model": self._name,
                "res_id": self.id,
                "company_id": company.id,
            }
        )
        self.sudo()._l10n_cl_write_dte_lifecycle_values(
            {
                "l10n_cl_dte_folio": folio,
                "l10n_cl_dte_caf_id": caf.id,
                "l10n_cl_dte_attachment_id": xml_attachment.id,
                "l10n_cl_dte_envelope_attachment_id": envelope_attachment.id,
                "l10n_cl_dte_status": "not_sent",
                "l10n_cl_dte_diagnostic": False,
            }
        )

    def action_l10n_cl_dte_send(self):
        self.ensure_one()
        self._check_dte_action_access()
        if self.l10n_cl_dte_is_external:
            raise UserError(
                _("This DTE was recovered from MiPyme and cannot be reissued.")
            )
        self._lock_dte_lifecycle()
        if self.state != "posted" or self.l10n_cl_dte_status != "not_sent":
            raise UserError(_("Only a posted, pending DTE can be sent."))
        if not self.sudo().l10n_cl_dte_envelope_attachment_id:
            raise UserError(_("The signed SII envelope is missing."))
        company = self.company_id.sudo()
        if company.l10n_cl_dte_environment == "SIIDEMO":
            result = {
                "track_id": f"DEMO-{self.id}-{self.l10n_cl_dte_folio}",
                "status": "ask_for_status",
                "diagnostic": _(
                    "Simulated submission in SIIDEMO. No SII request was made."
                ),
            }
        else:
            certificate = company._get_digital_signature(self.env.user.id)
            attachment = self.sudo().l10n_cl_dte_envelope_attachment_id.with_context(
                bin_size=False
            )
            payload = base64.b64decode(attachment.datas)
            try:
                result = SiiDteClient(company, certificate).send(
                    payload, attachment.name, self.l10n_cl_dte_type_code
                )
            except SiiTransportError as error:
                if error.outcome_unknown:
                    uncertain_values = {
                        "l10n_cl_dte_status": "error",
                        "l10n_cl_dte_diagnostic": _(str(error)),
                    }
                    if error.track_id:
                        uncertain_values["l10n_cl_dte_track_id"] = error.track_id
                    self._l10n_cl_write_dte_lifecycle_values(uncertain_values)
                    return True
                raise UserError(_(str(error))) from error
        self._l10n_cl_write_dte_lifecycle_values(
            {
                "l10n_cl_dte_track_id": result["track_id"],
                "l10n_cl_dte_status": result["status"],
                "l10n_cl_dte_diagnostic": result.get("diagnostic") or False,
            }
        )
        return True

    def action_l10n_cl_dte_check_status(self):
        self.ensure_one()
        self._check_dte_action_access()
        self._lock_dte_lifecycle()
        if (
            self.l10n_cl_dte_status not in ("ask_for_status", "error")
            or not self.l10n_cl_dte_track_id
        ):
            raise UserError(_("Only a DTE with a Track ID can be consulted."))
        company = self.company_id.sudo()
        if company.l10n_cl_dte_environment == "SIIDEMO":
            result = {
                "status": "accepted",
                "diagnostic": _(
                    "Simulated acceptance in SIIDEMO. No SII request was made."
                ),
            }
        else:
            certificate = company._get_digital_signature(self.env.user.id)
            try:
                result = SiiDteClient(company, certificate).check_status(
                    self.l10n_cl_dte_track_id,
                    self.l10n_cl_dte_type_code,
                    self.l10n_cl_dte_folio,
                )
            except SiiTransportError as error:
                error_values = {
                    "l10n_cl_dte_last_status_check": fields.Datetime.now(),
                    "l10n_cl_dte_last_status_error": str(error),
                }
                if error.response_metadata:
                    error_values.update(
                        {
                            "l10n_cl_dte_sii_status_code": error.response_metadata.get(
                                "sii_status_code"
                            )
                            or False,
                            "l10n_cl_dte_sii_server_code": error.response_metadata.get(
                                "sii_server_code"
                            )
                            or False,
                            "l10n_cl_dte_sii_sql_code": error.response_metadata.get(
                                "sii_sql_code"
                            )
                            or False,
                            "l10n_cl_dte_sii_error_code": error.response_metadata.get(
                                "sii_error_code"
                            )
                            or False,
                            "l10n_cl_dte_sii_token_code": error.response_metadata.get(
                                "sii_token_code"
                            )
                            or False,
                            "l10n_cl_dte_sii_attention_number": (
                                error.response_metadata.get("sii_attention_number")
                                or False
                            ),
                            "l10n_cl_dte_sii_document_type": False,
                            "l10n_cl_dte_sii_has_summary": False,
                            "l10n_cl_dte_sii_informed_count": False,
                            "l10n_cl_dte_sii_accepted_count": False,
                            "l10n_cl_dte_sii_rejected_count": False,
                            "l10n_cl_dte_sii_objection_count": False,
                        }
                    )
                self._l10n_cl_write_dte_lifecycle_values(error_values)
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": _("SII status query failed"),
                        "message": str(error),
                        "type": "warning",
                        "sticky": True,
                    },
                }
        self._l10n_cl_write_dte_lifecycle_values(
            {
                "l10n_cl_dte_status": result["status"],
                "l10n_cl_dte_last_status_check": fields.Datetime.now(),
                "l10n_cl_dte_last_status_error": False,
                "l10n_cl_dte_diagnostic": result.get("diagnostic") or False,
                "l10n_cl_dte_sii_status_code": result.get("sii_status_code") or False,
                "l10n_cl_dte_sii_server_code": result.get("sii_server_code") or False,
                "l10n_cl_dte_sii_sql_code": result.get("sii_sql_code") or False,
                "l10n_cl_dte_sii_error_code": result.get("sii_error_code") or False,
                "l10n_cl_dte_sii_token_code": result.get("sii_token_code") or False,
                "l10n_cl_dte_sii_attention_number": (
                    result.get("sii_attention_number") or False
                ),
                "l10n_cl_dte_sii_document_type": (
                    result.get("sii_document_type") or False
                ),
                "l10n_cl_dte_sii_has_summary": bool(result.get("sii_document_type")),
                "l10n_cl_dte_sii_informed_count": result.get(
                    "sii_informed_count", False
                ),
                "l10n_cl_dte_sii_accepted_count": result.get(
                    "sii_accepted_count", False
                ),
                "l10n_cl_dte_sii_rejected_count": result.get(
                    "sii_rejected_count", False
                ),
                "l10n_cl_dte_sii_objection_count": result.get(
                    "sii_objection_count", False
                ),
            }
        )
        return True

    def action_l10n_cl_dte_reconcile_uncertain(self):
        self.ensure_one()
        self._check_dte_action_access()
        if not self.env.user.has_group("l10n_cl_dte_sii.group_dte_manager"):
            raise UserError(
                _("Sólo un responsable DTE puede conciliar un envío incierto.")
            )
        if (
            self.state != "posted"
            or self.l10n_cl_dte_type_code not in ("33", "34")
            or self.l10n_cl_dte_is_external
            or self.l10n_cl_dte_status != "error"
            or self.l10n_cl_dte_track_id
        ):
            raise UserError(
                _(
                    "Sólo se puede conciliar una factura 33/34 contabilizada, "
                    "con envío incierto y sin TrackID."
                )
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Conciliar envío DTE incierto"),
            "res_model": "l10n_cl.dte.reconciliation.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_move_id": self.id},
        }

    def _lock_dte_lifecycle(self):
        """Serialize DTE lifecycle actions and refresh state after a conflict."""
        self.ensure_one()
        self.env.cr.execute(
            "SELECT pg_try_advisory_xact_lock(%s, %s)",
            [_DTE_LIFECYCLE_LOCK_NAMESPACE, self.id],
        )
        if not self.env.cr.fetchone()[0]:
            raise UserError(
                _(
                    "Otro usuario está enviando, consultando o conciliando "
                    "este DTE. Vuelva a intentarlo cuando termine."
                )
            )
        self.env.cr.execute(
            "SELECT id FROM account_move WHERE id = %s FOR UPDATE", [self.id]
        )
        self.invalidate_recordset(
            ["l10n_cl_dte_status", "l10n_cl_dte_track_id", "state"]
        )

    def action_l10n_cl_dte_review_objections(self):
        self.ensure_one()
        self._check_dte_action_access()
        if not self.l10n_cl_dte_diagnostic:
            raise UserError(_("There is no SII diagnosis available for this document."))
        return self.action_l10n_cl_dte_open_xml()

    def action_l10n_cl_dte_open_xml(self):
        self._check_dte_action_access()
        return self._open_dte_attachment(self.sudo().l10n_cl_dte_attachment_id)

    def action_l10n_cl_dte_open_envelope(self):
        self._check_dte_action_access()
        return self._open_dte_attachment(self.sudo().l10n_cl_dte_envelope_attachment_id)

    def action_l10n_cl_dte_send_to_receiver(self):
        """Queue a recipient-addressed copy only after an explicit user action."""
        self.ensure_one()
        self._check_dte_action_access()
        self._lock_dte_lifecycle()
        if (
            self.state != "posted"
            or self.move_type not in {"out_invoice", "out_refund"}
            or self.l10n_cl_dte_status not in {"accepted", "objected"}
            or self.l10n_cl_dte_is_external
        ):
            raise UserError(
                _(
                    "Sólo se puede enviar al receptor un DTE propio publicado "
                    "y aceptado por el SII."
                )
            )
        if self.l10n_cl_dte_receiver_delivery_status in {"sent", "legacy_sent"}:
            raise UserError(_("Este DTE ya figura como enviado al receptor."))
        active_delivery = self.env["l10n_cl.dte.receiver.delivery"].search(
            [("move_id", "=", self.id), ("state", "in", ["outgoing", "sent"])],
            limit=1,
        )
        if active_delivery:
            raise UserError(_("Ya existe un envío activo o completado para este DTE."))

        email = (
            self.partner_id.l10n_cl_dte_email
            or self.commercial_partner_id.l10n_cl_dte_email
        )
        if not email:
            raise UserError(
                _("Configure el correo DTE del receptor antes de continuar.")
            )
        if not self.l10n_cl_dte_attachment_id or not self.l10n_cl_dte_caf_id:
            raise UserError(_("Falta el DTE firmado o su CAF de origen."))
        certificate = self.company_id.sudo()._get_digital_signature(self.env.user.id)
        source_attachment = self.sudo().l10n_cl_dte_attachment_id.with_context(
            bin_size=False
        )
        try:
            signed_dte = etree.fromstring(
                base64.b64decode(source_attachment.datas, validate=True),
                parser=etree.XMLParser(resolve_entities=False, no_network=True),
            )
            signed_type_code = signed_dte.xpath(
                "string(.//*[local-name()='TipoDTE'][1])"
            )
            if signed_type_code != self.l10n_cl_dte_type_code:
                raise UserError(
                    _("El XML firmado no corresponde al tipo DTE de la factura.")
                )
            validate_dte_element(signed_dte)
            caf_root = self.l10n_cl_dte_caf_id.sudo()._decode_caf()
            builder = self._l10n_cl_dte_xml_builder(
                self.l10n_cl_dte_folio, caf_root, certificate
            )
            receiver_rut = (
                "55555555-5"
                if self.partner_id.l10n_cl_sii_taxpayer_type == "4"
                else self.partner_id.vat
            )
            if not receiver_rut:
                raise UserError(_("Configure el RUT del receptor antes de continuar."))
            envelope = builder.build_envelope(signed_dte, receiver_rut=receiver_rut)
        except UserError:
            raise
        except Exception as error:
            raise UserError(
                _("No fue posible preparar el DTE firmado para el receptor: %s", error)
            ) from error

        filename = (
            f"F{self.l10n_latam_document_number}T{self.l10n_cl_dte_type_code}.xml"
        )
        attachment = (
            self.env["ir.attachment"]
            .sudo()
            .create(
                {
                    "name": filename,
                    "type": "binary",
                    "datas": base64.b64encode(serialize_xml(envelope)),
                    "mimetype": "application/xml",
                    "res_model": self._name,
                    "res_id": self.id,
                    "company_id": self.company_id.id,
                }
            )
        )
        mail_id = self.env.ref("l10n_cl_dte_sii.mail_template_dte_receiver").send_mail(
            self.id,
            force_send=False,
            email_values={"email_to": email, "attachment_ids": [attachment.id]},
        )
        self.env["l10n_cl.dte.receiver.delivery"].sudo().with_context(
            _l10n_cl_create_receiver_delivery=True
        ).create(
            {
                "move_id": self.id,
                "recipient_email": email,
                "requested_at": fields.Datetime.now(),
                "actor_id": self.env.uid,
                "envelope_attachment_id": attachment.id,
                "mail_id": mail_id,
            }
        )
        self.message_post(
            body=_(
                "DTE dirigido al receptor agregado a la cola de correo para %s.",
                email,
            ),
            attachment_ids=[attachment.id],
            subtype_xmlid="mail.mt_note",
        )
        return True

    def _open_dte_attachment(self, attachment):
        self.ensure_one()
        self._check_dte_action_access()
        if not attachment or attachment.company_id not in self.env.companies:
            raise UserError(_("The signed XML is not available to this user."))
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{attachment.id}?download=true",
            "target": "download",
        }

    def _check_dte_action_access(self):
        self.ensure_one()
        self.check_access("read")
        if not self.env.user.has_group("l10n_cl_dte_sii.group_dte_user"):
            raise UserError(_("You are not allowed to operate SII DTE documents."))
        if self.company_id not in self.env.companies:
            raise UserError(_("You cannot access this document's company."))
