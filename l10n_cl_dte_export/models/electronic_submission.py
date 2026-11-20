# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..services.export_issuance_adapter import ExportIssuanceAdapter
from ..services.export_xml_composer import ExportXmlComposer


class ElectronicSubmission(models.Model):
    _inherit = "l10n_cl.electronic.submission"

    @api.model
    def _get_document_validator(self, document_code):
        """Validate export payloads with the Exportaciones rules."""
        if document_code in ExportIssuanceAdapter.SUPPORTED_DOCUMENT_CODES:
            return ExportXmlComposer()
        return super()._get_document_validator(document_code)


class ElectronicDocument(models.Model):
    _inherit = "l10n_cl.electronic.document"

    export_root_document_id = fields.Many2one(
        "l10n_cl.electronic.document",
        string="Root Export",
        readonly=True,
        copy=False,
        index=True,
        check_company=True,
        ondelete="restrict",
    )
    # Bumped only by the export note lock; it serializes notes of one root.
    export_revision = fields.Integer(readonly=True, copy=False, default=0)

    @api.constrains("document_code", "export_root_document_id")
    def _check_export_root(self):
        for document in self:
            # The issuance adapter always sets the root of an issued note.
            root = document.export_root_document_id
            if root and document.document_code not in {"111", "112"}:
                raise ValidationError(
                    self.env._("Only export notes 111 and 112 have a root export.")
                )
            if root and (
                root.document_code != "110" or root.company_id != document.company_id
            ):
                raise ValidationError(
                    self.env._("An export note must refer to an export 110.")
                )

    def write(self, vals):
        if "export_revision" in vals or (
            "export_root_document_id" in vals
            and any(
                document.export_root_document_id.id != vals["export_root_document_id"]
                for document in self
            )
        ):
            raise ValidationError(
                self.env._("The root export of a document cannot change.")
            )
        return super().write(vals)
