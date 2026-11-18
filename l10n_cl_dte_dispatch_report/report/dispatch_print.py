# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, models
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_report.report.dte_document_report import (
    render_ted_barcode,
)

from .dispatch_print_values import extract_guide_print_values


class ReportDispatchPrint(models.AbstractModel):
    _name = "report.l10n_cl_dte_dispatch_report.dispatch_print"
    _description = "DTE 52 dispatch guide print"

    def _get_report_values(self, docids, data=None):
        samples = self.env["l10n_cl.dte.report.sample"].browse(docids).exists()
        if len(samples) != len(docids):
            raise ValidationError(_("One or more print samples no longer exist."))
        return {
            "doc_ids": samples.ids,
            "doc_model": samples._name,
            "docs": samples,
            "guides": {sample.id: self._guide_values(sample) for sample in samples},
        }

    @api.model
    def _guide_values(self, sample):
        values = extract_guide_print_values(sample._get_signed_xml())
        values["barcode"] = self._barcode(values["ted"])
        values["environment"] = sample.document_id.sii_environment
        return values

    @api.model
    def _barcode(self, ted):
        """Return the compact TED barcode as a base64 PNG."""
        return render_ted_barcode(ted)
