# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

MAX_PACKAGES = 10
# The root 110 reference is generated on issuance and counts toward the 40.
MAX_SUPPORTING_REFERENCES = 39
SUPPORTING_TYPES = frozenset(str(code) for code in range(801, 814))
UNIQUE_SUPPORTING_TYPES = frozenset({"807", "811", "812"})


class ExportChildMixin(models.AbstractModel):
    """Invoice-owned export rows, frozen with the export inputs of the move."""

    _name = "l10n_cl.dte.export.child.mixin"
    _description = "Export Invoice Child"
    _order = "sequence, id"
    _check_company_auto = True

    move_id = fields.Many2one(
        "account.move",
        required=True,
        index=True,
        ondelete="cascade",
        check_company=True,
    )
    company_id = fields.Many2one(
        related="move_id.company_id", store=True, index=True, readonly=True
    )
    sequence = fields.Integer(default=10)

    @api.model_create_multi
    def create(self, vals_list):
        moves = self.env["account.move"].browse(
            [vals.get("move_id") for vals in vals_list]
        )
        moves._check_export_input_mutation("write")
        records = super().create(vals_list)
        records.move_id._check_export_children()
        return records

    def write(self, vals):
        moves = self.move_id
        if vals.get("move_id"):
            moves |= self.env["account.move"].browse(vals["move_id"])
        moves._check_export_input_mutation("write")
        result = super().write(vals)
        moves._check_export_children()
        return result

    def unlink(self):
        self.move_id._check_export_input_mutation("write")
        return super().unlink()


class ExportPackage(models.Model):
    """A package declared on the export invoice (TipoBultos), not stock packing."""

    _name = "l10n_cl.dte.export.package"
    _inherit = "l10n_cl.dte.export.child.mixin"
    _description = "Export Invoice Package"

    type_id = fields.Many2one(
        "l10n_cl.dte.export.catalog",
        string="Package Type",
        required=True,
        ondelete="restrict",
        domain=[("kind", "=", "package")],
    )
    quantity = fields.Integer(required=True)
    marks = fields.Char(size=255)
    container = fields.Char(size=25)
    seal = fields.Char(size=20)
    seal_issuer = fields.Char(size=70)

    @api.constrains("type_id", "quantity")
    def _check_package(self):
        for package in self:
            if package.type_id.kind != "package":
                raise ValidationError(_("Choose a customs package type."))
            if package.quantity <= 0:
                raise ValidationError(_("A package quantity must be positive."))


class ExportReference(models.Model):
    """A supporting document of the export (DUS, bill of lading, ...)."""

    _name = "l10n_cl.dte.export.reference"
    _inherit = "l10n_cl.dte.export.child.mixin"
    _description = "Export Invoice Supporting Reference"

    document_type = fields.Char(required=True, size=3)
    folio = fields.Char(required=True, size=18)
    date = fields.Date(required=True)
    reason = fields.Char(size=90)

    @api.constrains("document_type")
    def _check_reference_type(self):
        for reference in self:
            if reference.document_type not in SUPPORTING_TYPES:
                raise ValidationError(
                    _("Supporting export references use document types 801 to 813.")
                )
