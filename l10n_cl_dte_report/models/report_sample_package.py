# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
import json
from io import BytesIO

from pypdf import PdfReader

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class DteReportSamplePackage(models.Model):
    _name = "l10n_cl.dte.report.sample.package"
    _description = "Chilean DTE Print Sample Package"
    _order = "create_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, copy=False, index=True)
    company_id = fields.Many2one(
        "res.company", required=True, index=True, ondelete="restrict"
    )
    sample_ids = fields.Many2many(
        "l10n_cl.dte.report.sample",
        relation="l10n_cl_dte_report_sample_package_rel",
        column1="package_id",
        column2="sample_id",
        required=True,
        copy=False,
    )
    sample_count = fields.Integer(compute="_compute_sample_count", store=True)
    state = fields.Selection(
        [("draft", "Draft"), ("generated", "Generated")],
        required=True,
        default="draft",
        readonly=True,
        copy=False,
        index=True,
    )
    pdf_content = fields.Binary(
        attachment=True,
        readonly=True,
        copy=False,
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    pdf_filename = fields.Char(readonly=True, copy=False)
    pdf_sha256 = fields.Char(readonly=True, copy=False, index=True)
    manifest = fields.Json(readonly=True, copy=False)
    generated_at = fields.Datetime(readonly=True, copy=False)

    @api.depends("sample_ids")
    def _compute_sample_count(self):
        for package in self:
            package.sample_count = len(package.sample_ids)

    @api.constrains("company_id", "sample_ids")
    def _check_package_scope(self):
        for package in self:
            if len(package.sample_ids) > 20:
                raise ValidationError(
                    _("A printed sample package can contain at most 20 DTEs.")
                )
            if any(
                sample.company_id != package.company_id for sample in package.sample_ids
            ):
                raise ValidationError(
                    _("All print samples must belong to the package company.")
                )

    @api.model_create_multi
    def create(self, vals_list):
        self._check_manager()
        protected = {
            "state",
            "pdf_content",
            "pdf_filename",
            "pdf_sha256",
            "manifest",
            "generated_at",
        }
        if any(protected.intersection(values) for values in vals_list):
            raise AccessError(_("Package output fields are assigned by the system."))
        return super().create(vals_list)

    def write(self, vals):
        self._check_manager()
        if self.filtered(lambda package: package.state == "generated"):
            raise AccessError(_("A generated sample package is immutable."))
        if {
            "state",
            "pdf_content",
            "pdf_filename",
            "pdf_sha256",
            "manifest",
            "generated_at",
        }.intersection(vals):
            raise AccessError(_("Package output fields can only be set by its action."))
        return super().write(vals)

    def unlink(self):
        self._check_manager()
        if self.filtered(lambda package: package.state == "generated"):
            raise AccessError(_("A generated sample package cannot be deleted."))
        return super().unlink()

    def _check_manager(self):
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(_("Only a DTE manager can manage print sample packages."))

    def action_generate_package(self):
        self.ensure_one()
        self._check_manager()
        if self.state != "draft":
            raise UserError(_("This print sample package has already been generated."))
        if not self.sample_ids or len(self.sample_ids) > 20:
            raise ValidationError(
                _("Select between one and 20 print samples for the package.")
            )
        if self.sample_ids.filtered(lambda sample: sample.state != "generated"):
            raise ValidationError(
                _("Generate every individual DTE sample before packaging it.")
            )
        for sample in self.sample_ids:
            pdf_bytes = base64.b64decode(sample.pdf_content, validate=True)
            if hashlib.sha256(pdf_bytes).hexdigest() != sample.pdf_sha256:
                raise ValidationError(
                    _("A stored print sample does not match its recorded SHA-256.")
                )
        if any(sample.company_id != self.company_id for sample in self.sample_ids):
            raise ValidationError(
                _("All print samples must belong to the package company.")
            )
        ordered_samples = self.sample_ids.sorted(
            key=lambda sample: (sample.document_code, sample.folio, sample.id)
        )
        report = self.env.ref("l10n_cl_dte_report.action_dte_print_sample")
        pdf, _content_type = self.env["ir.actions.report"]._render_qweb_pdf(
            report.id, res_ids=ordered_samples.ids
        )
        if not pdf.startswith(b"%PDF"):
            raise ValidationError(_("The sample package did not produce a PDF."))
        if len(PdfReader(BytesIO(pdf)).pages) != len(ordered_samples):
            raise ValidationError(
                _("The package must render exactly one page per DTE sample.")
            )
        manifest = {
            "schema": "l10n_cl_dte_report.package/1",
            "company_id": self.company_id.id,
            "samples": [
                {
                    "sample_id": sample.id,
                    "document_code": sample.document_code,
                    "folio": sample.folio,
                    "signed_xml_sha256": sample.signed_artifact_id.sha256,
                    "pdf_sha256": sample.pdf_sha256,
                }
                for sample in ordered_samples
            ],
        }
        super().write(
            {
                "state": "generated",
                "pdf_content": base64.b64encode(pdf),
                "pdf_filename": f"{self.name}.pdf",
                "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
                "manifest": json.loads(json.dumps(manifest)),
                "generated_at": fields.Datetime.now(),
            }
        )
        return True

    def action_download_pdf(self):
        self.ensure_one()
        self._check_manager()
        if self.state != "generated" or not self.pdf_content:
            raise UserError(_("Generate the sample package before downloading it."))
        return {
            "type": "ir.actions.act_url",
            "url": (
                f"/web/content/{self._name}/{self.id}/pdf_content/"
                f"{self.pdf_filename}?download=true"
            ),
            "target": "self",
        }
