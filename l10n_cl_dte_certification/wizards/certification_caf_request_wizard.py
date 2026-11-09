# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Explicit multi-document CAF request wizard."""

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

SUPPORTED_CAF_CODES = ("33", "34", "56", "61")


class CertificationCafRequestWizard(models.TransientModel):
    _name = "l10n_cl.dte.certification.caf.request.wizard"
    _description = "Request Certification CAFs"

    set_id = fields.Many2one(
        "l10n_cl.dte.certification.set", required=True, readonly=True
    )
    available_document_type_ids = fields.Many2many(
        "l10n_latam.document.type",
        compute="_compute_available_document_type_ids",
    )
    line_ids = fields.One2many(
        "l10n_cl.dte.certification.caf.request.wizard.line", "wizard_id"
    )

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        if "line_ids" not in field_names or "line_ids" in values:
            return values
        certification_set = (
            self.env["l10n_cl.dte.certification.set"]
            .browse(values.get("set_id") or self.env.context.get("default_set_id"))
            .exists()
        )
        if not certification_set:
            return values
        certification_set._check_certification_access()
        document_types = self.env["l10n_latam.document.type"].search(
            [
                ("country_id", "=", certification_set.company_id.country_id.id),
                ("code", "in", SUPPORTED_CAF_CODES),
            ]
        )
        values["line_ids"] = []
        for item in certification_set._get_folio_requirements():
            if not item["missing"]:
                continue
            document_type = document_types.filtered(
                lambda document, code=item["document_code"]: document.code == code
            )[:1]
            if document_type:
                values["line_ids"].append(
                    Command.create(
                        {
                            "document_type_id": document_type.id,
                            "requested_quantity": item["missing"],
                        }
                    )
                )
        return values

    @api.depends("set_id", "set_id.case_ids.document_code", "set_id.company_id")
    def _compute_available_document_type_ids(self):
        for wizard in self:
            codes = set(wizard.set_id.case_ids.mapped("document_code"))
            codes.intersection_update(SUPPORTED_CAF_CODES)
            wizard.available_document_type_ids = self.env[
                "l10n_latam.document.type"
            ].search(
                [
                    ("code", "in", sorted(codes)),
                    ("country_id", "=", wizard.set_id.company_id.country_id.id),
                ]
            )

    def action_confirm_request(self):
        """Create a queued request only after the explicit confirmation click."""
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(_("Only a DTE manager can request CAFs."))
        certification_set = self.set_id
        certification_set._check_certification_access()
        certification_set._verify_source_integrity()
        certification_set._lock_configuration()
        if not certification_set.can_edit_envelope:
            raise UserError(_("CAF configuration is frozen for this set."))
        if (
            certification_set.company_id.dte_sii_environment != "certification"
            or not certification_set.company_id.dte_certification_enabled
        ):
            raise UserError(_("CAF requests are restricted to Certification."))
        if not self.line_ids:
            raise ValidationError(_("Add at least one document type and quantity."))
        quantities = [
            (line.document_type_id, line.requested_quantity) for line in self.line_ids
        ]
        request = self.env["l10n_cl.dte.certification.caf.request"]._create_from_wizard(
            certification_set, quantities
        )
        return request.action_open()


class CertificationCafRequestWizardLine(models.TransientModel):
    _name = "l10n_cl.dte.certification.caf.request.wizard.line"
    _description = "Certification CAF Request Wizard Line"
    _sql_constraints = [
        (
            "wizard_document_type_uniq",
            "unique(wizard_id, document_type_id)",
            "A document type can only be selected once.",
        ),
        (
            "requested_quantity_positive",
            "CHECK(requested_quantity > 0)",
            "Requested quantities must be positive.",
        ),
    ]

    wizard_id = fields.Many2one(
        "l10n_cl.dte.certification.caf.request.wizard",
        required=True,
        ondelete="cascade",
    )
    document_type_id = fields.Many2one(
        "l10n_latam.document.type", required=True, ondelete="restrict"
    )
    requested_quantity = fields.Integer(required=True)

    @api.constrains("wizard_id", "document_type_id", "requested_quantity")
    def _check_request_line(self):
        for line in self:
            if line.requested_quantity < 1:
                raise ValidationError(_("Requested quantities must be positive."))
            if line.document_type_id not in line.wizard_id.available_document_type_ids:
                raise ValidationError(
                    _("Only CAF document types present in the set can be requested.")
                )
