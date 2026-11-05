# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, models
from odoo.exceptions import ValidationError


class ReportBoletaReceipt(models.AbstractModel):
    _name = "report.l10n_cl_dte_report.boleta_receipt"
    _inherit = "report.l10n_cl_dte_report.dte_print_sample"
    _description = "Chilean Boleta Thermal Receipt"

    def _get_report_values(self, docids, data=None):
        samples = self.env["l10n_cl.dte.report.sample"].browse(docids).exists()
        samples._check_manager()
        if any(sample.document_code not in {"39", "41"} for sample in samples):
            raise ValidationError(
                _("Thermal printing supports only boletas 39 and 41.")
            )
        return super()._get_report_values(docids, data=data)
