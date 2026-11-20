# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo import _, api, models
from odoo.exceptions import UserError

from .dispatch_print_values import extract_guide_print_values


class ReportDispatchGuide(models.AbstractModel):
    """Print a dispatch guide with its stamp, for any DTE user."""

    _name = "report.l10n_cl_dte_dispatch_report.dispatch_guide"
    _description = "DTE 52 dispatch guide"

    def _get_report_values(self, docids, data=None):
        dispatches = self.env["l10n_cl.dte.dispatch"].browse(docids).exists()
        dispatches.check_access("read")
        return {
            "doc_ids": dispatches.ids,
            "doc_model": dispatches._name,
            "docs": dispatches,
            "guides": {
                dispatch.id: self._guide_values(dispatch) for dispatch in dispatches
            },
        }

    @api.model
    def _guide_values(self, dispatch):
        documents = dispatch.sudo().document_ids.filtered(
            lambda document: document.preparation_state in {"signed", "queued"}
            and document.fiscal_state != "rejected"
        )
        if not documents:
            raise UserError(
                _("Dispatch guide %(name)s has no signed DTE to print.")
                % {"name": dispatch.name}
            )
        artifact = documents.sorted("id")[-1]._get_signed_artifact()
        artifact.action_verify_integrity()
        signed = base64.b64decode(
            artifact.with_context(bin_size=False).content, validate=True
        )
        printer = self.env["report.l10n_cl_dte_dispatch_report.dispatch_print"]
        values = extract_guide_print_values(signed)
        values["barcode"] = printer._barcode(values["ted"])
        values["environment"] = documents.sorted("id")[-1].sii_environment
        return values
