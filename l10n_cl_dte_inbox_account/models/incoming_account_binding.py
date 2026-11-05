# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services.binding_context import trusted
from ..services.common import GROUP, canonical_folio, reject, vat


class IncomingDteAccountBinding(models.Model):
    _name = "l10n_cl.incoming.dte.account.binding"
    _description = "Incoming DTE Vendor Bill Binding"
    _check_company_auto = True
    _rec_name = "folio"
    _order = "linked_at desc, id desc"

    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, index=True, ondelete="restrict"
    )
    incoming_document_id = fields.Many2one(
        "l10n_cl.incoming.dte",
        required=True,
        readonly=True,
        check_company=True,
        index=True,
        ondelete="restrict",
    )
    move_id = fields.Many2one(
        "account.move",
        required=True,
        readonly=True,
        check_company=True,
        index=True,
        ondelete="restrict",
    )
    partner_id = fields.Many2one(
        "res.partner",
        required=True,
        readonly=True,
        check_company=True,
        ondelete="restrict",
    )
    issuer_vat = fields.Char(required=True, readonly=True, index=True)
    document_code = fields.Selection(
        [("33", "Electronic Invoice"), ("34", "Exempt Electronic Invoice")],
        required=True,
        readonly=True,
    )
    folio = fields.Char(required=True, readonly=True, index=True)
    issue_date = fields.Date(required=True, readonly=True)
    sii_environment = fields.Selection(
        [
            ("demo", "Demo"),
            ("certification", "Certification"),
            ("production", "Production"),
        ],
        required=True,
        readonly=True,
    )
    xml_sha256 = fields.Char(required=True, readonly=True, copy=False)
    source_snapshot = fields.Json(required=True, readonly=True, copy=False)
    mapping_snapshot = fields.Json(required=True, readonly=True, copy=False)
    parser_version = fields.Char(
        required=True, readonly=True, default="incoming_account_v1"
    )
    operation = fields.Selection(
        [("create", "Created Draft"), ("link", "Linked Draft")],
        required=True,
        readonly=True,
    )
    linked_at = fields.Datetime(
        required=True, readonly=True, default=fields.Datetime.now
    )
    linked_by_id = fields.Many2one(
        "res.users", required=True, readonly=True, ondelete="restrict"
    )
    move_state = fields.Selection(
        related="move_id.state", string="Bill Status", readonly=True
    )

    _sql_constraints = [
        (
            "incoming_document_uniq",
            "unique(incoming_document_id)",
            "This incoming DTE already has a vendor bill link.",
        ),
        (
            "move_uniq",
            "unique(move_id)",
            "This vendor bill already has an incoming DTE link.",
        ),
        (
            "fiscal_identity_uniq",
            "unique(company_id, issuer_vat, document_code, folio)",
            ("This supplier DTE identity already has a vendor bill link."),
        ),
        (
            "folio_positive",
            "check(folio ~ '^[1-9][0-9]*$')",
            "The DTE folio must be canonical and positive.",
        ),
    ]

    @api.constrains(
        "company_id",
        "incoming_document_id",
        "move_id",
        "partner_id",
        "issuer_vat",
        "document_code",
        "folio",
    )
    def _check_binding_consistency(self):
        for binding in self:
            incoming, move = binding.incoming_document_id, binding.move_id
            if (
                incoming.company_id != binding.company_id
                or move.company_id != binding.company_id
                or move.move_type != "in_invoice"
                or binding.partner_id != move.commercial_partner_id
                or binding.issuer_vat != vat(incoming.issuer_vat)
                or binding.issuer_vat != vat(binding.partner_id.vat)
                or binding.folio != canonical_folio(incoming.folio)
                or binding.folio != canonical_folio(move.l10n_latam_document_number)
                or binding.document_code != incoming.document_code
                or binding.document_code != move.l10n_latam_document_type_id.code
                or binding.xml_sha256 != incoming.xml_sha256
            ):
                reject(self.env, "identity_mismatch")

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su or not trusted(self.env):
            raise AccessError(
                self.env._(
                    "Vendor bill bindings can only be created through confirmation."
                )
            )
        return super().create(vals_list)

    def write(self, vals):  # pylint: disable=method-required-super
        raise UserError(
            self.env._("The incoming DTE vendor bill provenance is immutable.")
        )

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(
            self.env._("The incoming DTE vendor bill provenance cannot be deleted.")
        )

    def action_open_move(self):
        self.ensure_one()
        self.check_access("read")
        if not self.env.user.has_group(GROUP):
            raise AccessError(
                self.env._("The incoming DTE vendor bill role is required.")
            )
        self.move_id.check_access("read")
        return self.move_id.get_formview_action()

    def action_open_incoming(self):
        self.ensure_one()
        self.check_access("read")
        if not self.env.user.has_group(GROUP):
            raise AccessError(
                self.env._("The incoming DTE vendor bill role is required.")
            )
        self.incoming_document_id.check_access("read")
        return self.incoming_document_id.get_formview_action()
