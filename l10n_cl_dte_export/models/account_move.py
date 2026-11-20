# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services.export_issuance_adapter import ExportIssuanceAdapter
from .export_children import (
    MAX_PACKAGES,
    MAX_SUPPORTING_REFERENCES,
    UNIQUE_SUPPORTING_TYPES,
)

EXPORT_CODES = {"110", "111", "112"}
CATALOG = "l10n_cl.dte.export.catalog"
# Customs catalog fields and the catalog category each one must use.
CATALOG_FIELDS = {
    "dte_export_payment_id": "payment",
    "dte_export_sale_mode_id": "sale_mode",
    "dte_export_sale_clause_id": "sale_clause",
    "dte_export_transport_id": "transport",
    "dte_export_loading_port_id": "port",
    "dte_export_unloading_port_id": "port",
    "dte_export_tare_unit_id": "unit",
    "dte_export_gross_unit_id": "unit",
    "dte_export_net_unit_id": "unit",
}
NONNEGATIVE_FIELDS = (
    "dte_export_clause_total",
    "dte_export_tare",
    "dte_export_gross_weight",
    "dte_export_net_weight",
    "dte_export_freight",
    "dte_export_insurance",
)
MAX_TARE = 9_999_999


def _catalog(string, kind):
    return fields.Many2one(
        CATALOG, string=string, ondelete="restrict", domain=[("kind", "=", kind)]
    )


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
    # Customs data of the Aduana block (SII DTE format, export documents).
    dte_export_payment_id = _catalog("Export Payment Method", "payment")
    dte_export_sale_mode_id = _catalog("Sale Mode", "sale_mode")
    dte_export_sale_clause_id = _catalog("Sale Clause", "sale_clause")
    dte_export_clause_total = fields.Monetary(
        string="Sale Clause Total", currency_field="currency_id"
    )
    dte_export_transport_id = _catalog("Transport Mode", "transport")
    dte_export_transport_name = fields.Char(string="Ship or Vehicle", size=40)
    dte_export_transport_extra = fields.Char(string="Additional Transport ID", size=20)
    dte_export_carrier_vat = fields.Char(
        string="Carrier RUT",
        size=10,
        help="Chilean RUT without dots, as in the XML: 76086428-5.",
    )
    dte_export_carrier_name = fields.Char(string="Carrier Name", size=40)
    dte_export_booking = fields.Char(string="Booking", size=20)
    dte_export_operator = fields.Char(string="Operator", size=20)
    dte_export_loading_port_id = _catalog("Loading Port", "port")
    dte_export_loading_extra = fields.Char(string="Additional Loading Port ID", size=20)
    dte_export_unloading_port_id = _catalog("Unloading Port", "port")
    dte_export_unloading_extra = fields.Char(
        string="Additional Unloading Port ID", size=20
    )
    dte_export_tare = fields.Integer(string="Tare")
    dte_export_tare_unit_id = _catalog("Tare Unit", "unit")
    dte_export_gross_weight = fields.Float(string="Gross Weight", digits=(10, 2))
    dte_export_gross_unit_id = _catalog("Gross Weight Unit", "unit")
    dte_export_net_weight = fields.Float(string="Net Weight", digits=(10, 2))
    dte_export_net_unit_id = _catalog("Net Weight Unit", "unit")
    dte_export_freight = fields.Monetary(string="Freight", currency_field="currency_id")
    dte_export_insurance = fields.Monetary(
        string="Insurance", currency_field="currency_id"
    )

    dte_export_package_ids = fields.One2many(
        "l10n_cl.dte.export.package", "move_id", string="Export Packages", copy=True
    )
    dte_export_reference_ids = fields.One2many(
        "l10n_cl.dte.export.reference",
        "move_id",
        string="Export Supporting References",
        copy=True,
    )

    def _check_export_children(self):
        """Limits of the export XML on packages and supporting references."""
        for move in self.exists():
            if len(move.dte_export_package_ids) > MAX_PACKAGES:
                raise ValidationError(_("An export declares at most ten packages."))
            references = move.dte_export_reference_ids
            if len(references) > MAX_SUPPORTING_REFERENCES:
                raise ValidationError(
                    _("An export carries at most 39 supporting references.")
                )
            keys = [(ref.document_type, ref.folio, ref.date) for ref in references]
            if len(keys) != len(set(keys)):
                raise ValidationError(_("A supporting reference is repeated."))
            for code in UNIQUE_SUPPORTING_TYPES:
                if (
                    len(references.filtered(lambda ref, c=code: ref.document_type == c))
                    > 1
                ):
                    raise ValidationError(
                        _("Document type %(code)s appears at most once.")
                        % {"code": code}
                    )

    # Export notes 111/112 and the correction they make on the root 110.
    dte_export_target_id = fields.Many2one(
        "l10n_cl.electronic.document",
        string="Root Export DTE",
        copy=False,
        index=True,
        check_company=True,
        ondelete="restrict",
        domain=[("document_code", "=", "110"), ("fiscal_state", "=", "accepted")],
    )
    dte_export_correction = fields.Selection(
        [("1", "Cancel export"), ("2", "Correct text"), ("3", "Correct amounts")],
        string="Export Correction",
        copy=False,
    )
    dte_export_reason = fields.Char(
        string="Export Correction Reason", size=90, copy=False
    )

    @api.constrains(*CATALOG_FIELDS, *NONNEGATIVE_FIELDS, "dte_export_carrier_vat")
    def _check_export_customs_inputs(self):
        caf = self.env["l10n_cl.caf.authorization"]
        for move in self:
            for name, kind in CATALOG_FIELDS.items():
                if move[name] and move[name].kind != kind:
                    raise ValidationError(
                        _("%(field)s needs a %(kind)s customs code.")
                        % {"field": move._fields[name].string, "kind": kind}
                    )
            if any(move[name] < 0 for name in NONNEGATIVE_FIELDS):
                raise ValidationError(
                    _("Customs amounts and weights cannot be negative.")
                )
            if move.dte_export_tare > MAX_TARE:
                raise ValidationError(_("The tare has at most seven digits."))
            carrier = move.dte_export_carrier_vat
            if carrier and not caf._is_valid_vat(caf._normalize_vat(carrier)):
                raise ValidationError(_("The carrier RUT is not a valid Chilean RUT."))

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

    def _get_dte_issuance_adapter(self, document_code):
        """Issue export invoices 110 through the export format adapter."""
        self.ensure_one()
        if str(document_code) in ExportIssuanceAdapter.SUPPORTED_DOCUMENT_CODES:
            return ExportIssuanceAdapter()
        return super()._get_dte_issuance_adapter(document_code)

    def _get_dte_allowed_move_types(self, document_code):
        """Export 110 and 111 are invoices; the credit note 112 is a refund."""
        if str(document_code) in ExportIssuanceAdapter.SUPPORTED_DOCUMENT_CODES:
            return ("out_refund",) if str(document_code) == "112" else ("out_invoice",)
        return super()._get_dte_allowed_move_types(document_code)

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
