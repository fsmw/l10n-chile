# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
import re
from io import BytesIO

from lxml import etree
from pypdf import PdfReader

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.l10n_cl_dte_boleta.services.boleta_xml_composer import (
    BoletaXmlComposer,
)
from odoo.addons.l10n_cl_dte_note.services.note_xml_composer import NoteXmlComposer
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import DteXmlComposer

_SUPPORTED_CODES = {"33", "34", "39", "41", "61", "56"}


class DteReportSample(models.Model):
    _name = "l10n_cl.dte.report.sample"
    _description = "Chilean DTE Print Sample"
    _order = "create_date desc, id desc"
    _check_company_auto = True
    _sql_constraints = [
        (
            "document_sample_uniq",
            "unique(document_id)",
            "A DTE can only have one report sample.",
        ),
    ]

    name = fields.Char(required=True, copy=False, index=True)
    company_id = fields.Many2one(
        "res.company", required=True, index=True, ondelete="restrict"
    )
    document_id = fields.Many2one(
        "l10n_cl.electronic.document",
        required=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    signed_artifact_id = fields.Many2one(
        "l10n_cl.electronic.artifact",
        required=True,
        readonly=True,
        ondelete="restrict",
        check_company=True,
    )
    document_code = fields.Char(related="document_id.document_code", store=True)
    folio = fields.Integer(related="document_id.folio", store=True)
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
    generated_at = fields.Datetime(readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        self._check_manager()
        normalized = []
        for incoming in vals_list:
            values = incoming.copy()
            if {
                "state",
                "pdf_content",
                "pdf_filename",
                "pdf_sha256",
                "generated_at",
                "signed_artifact_id",
                "company_id",
            }.intersection(values):
                raise AccessError(_("Sample output fields are assigned by the system."))
            document = (
                self.env["l10n_cl.electronic.document"]
                .browse(values.get("document_id"))
                .exists()
            )
            if len(document) != 1:
                raise ValidationError(_("Select one existing electronic document."))
            if document.company_id not in self.env.companies:
                raise AccessError(_("You cannot create samples for this company."))
            if document.document_code not in _SUPPORTED_CODES:
                raise UserError(
                    _("Printed samples are not yet supported for this DTE type.")
                )
            if document.preparation_state != "queued":
                raise ValidationError(
                    _("A print sample requires a prepared and signed DTE.")
                )
            artifacts = document.artifact_ids.filtered(
                lambda item: item.role == "signed_xml"
            )
            if len(artifacts) != 1:
                raise ValidationError(
                    _("The DTE must have exactly one signed XML evidence artifact.")
                )
            artifact = artifacts
            artifact.action_verify_integrity()
            values.update(
                {
                    "name": values.get("name") or document.name,
                    "company_id": document.company_id.id,
                    "signed_artifact_id": artifact.id,
                }
            )
            normalized.append(values)
        return super().create(normalized)

    def write(self, vals):
        self._check_manager()
        if self.filtered(lambda sample: sample.state == "generated"):
            raise AccessError(_("A generated print sample is immutable."))
        protected = {
            "state",
            "pdf_content",
            "pdf_filename",
            "pdf_sha256",
            "generated_at",
            "signed_artifact_id",
            "company_id",
            "document_id",
        }
        if protected.intersection(vals):
            raise AccessError(_("Sample output fields can only be set by its action."))
        return super().write(vals)

    def unlink(self):
        self._check_manager()
        if self.filtered(lambda sample: sample.state == "generated"):
            raise AccessError(_("A generated print sample cannot be deleted."))
        return super().unlink()

    def _check_manager(self):
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(_("Only a DTE manager can manage print samples."))

    def _get_signed_xml(self):
        self.ensure_one()
        artifact = self.signed_artifact_id
        if artifact.document_id != self.document_id or artifact.role != "signed_xml":
            raise ValidationError(_("The signed XML evidence does not match the DTE."))
        artifact.action_verify_integrity()
        try:
            content = base64.b64decode(artifact.content, validate=True)
        except (ValueError, TypeError) as error:
            raise ValidationError(_("The signed XML artifact is invalid.")) from error
        if self.document_code in {"39", "41"}:
            composer = BoletaXmlComposer()
        elif self.document_code in {"61", "56"}:
            composer = NoteXmlComposer()
        else:
            composer = DteXmlComposer()
        composer.validate_document(content)
        root = etree.fromstring(
            content,
            etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False),
        )
        namespace = {"sii": "http://www.sii.cl/SiiDte"}
        header = root.find("sii:Documento/sii:Encabezado/sii:IdDoc", namespace)
        if header is None or (
            header.findtext("sii:TipoDTE", namespaces=namespace) != self.document_code
            or header.findtext("sii:Folio", namespaces=namespace) != str(self.folio)
        ):
            raise ValidationError(_("The signed XML identity does not match the DTE."))
        return content

    def action_print_thermal(self):
        """Derive an 80 mm receipt from signed evidence without changing the sample."""
        self.ensure_one()
        self._check_manager()
        if self.document_code not in {"39", "41"}:
            raise UserError(_("Thermal printing supports only boletas 39 and 41."))
        self._get_signed_xml()
        return self.env.ref("l10n_cl_dte_report.action_boleta_receipt").report_action(
            self
        )

    def action_generate_pdf(self):
        self.ensure_one()
        self._check_manager()
        if self.state != "draft":
            raise UserError(_("This print sample has already been generated."))
        self._get_signed_xml()
        report = self.env.ref("l10n_cl_dte_report.action_dte_print_sample")
        pdf, _content_type = self.env["ir.actions.report"]._render_qweb_pdf(
            report.id, res_ids=self.ids
        )
        if not pdf.startswith(b"%PDF"):
            raise ValidationError(_("The DTE print report did not produce a PDF."))
        if len(PdfReader(BytesIO(pdf)).pages) != 1:
            raise ValidationError(
                _("Each DTE print sample must fit on exactly one page.")
            )
        super().write(
            {
                "state": "generated",
                "pdf_content": base64.b64encode(pdf),
                "pdf_filename": f"dte-{self.document_code}-{self.folio}.pdf",
                "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
                "generated_at": fields.Datetime.now(),
            }
        )
        return True

    def action_download_pdf(self):
        self.ensure_one()
        self._check_manager()
        if self.state != "generated" or not self.pdf_content:
            raise UserError(_("Generate the print sample before downloading it."))
        return {
            "type": "ir.actions.act_url",
            "url": (
                f"/web/content/{self._name}/{self.id}/pdf_content/"
                f"{self.pdf_filename}?download=true"
            ),
            "target": "self",
        }


def _extract_ted_bytes(signed_xml):
    """Return the literal TED bytes so the barcode never reserializes the DTE."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    try:
        root = etree.fromstring(signed_xml, parser)
    except (etree.XMLSyntaxError, ValueError) as error:
        raise ValidationError(_("The signed DTE XML is malformed.")) from error
    if etree.QName(root).localname != "DTE":
        raise ValidationError(_("The signed XML root must be a DTE."))
    starts = list(re.finditer(rb"<(?:[\w.-]+:)?TED(?:\s[^>]*)?>", signed_xml))
    ends = list(re.finditer(rb"</(?:[\w.-]+:)?TED\s*>", signed_xml))
    if len(starts) != 1 or len(ends) != 1 or ends[0].start() <= starts[0].start():
        raise ValidationError(_("The signed DTE must contain exactly one TED."))
    ted_bytes = signed_xml[starts[0].start() : ends[0].end()]
    try:
        ted_bytes.decode("ascii")
    except UnicodeDecodeError as error:
        raise ValidationError(
            _("The TED must contain only ASCII characters for PDF417 rendering.")
        ) from error
    return ted_bytes
