# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import binascii
import hashlib

from odoo import fields, models
from odoo.exceptions import UserError

from ..models.rcv_period import DOCUMENT_OPERATIONS
from ..services.rcv_csv_parser import RcvCsvParser
from ..services.rcv_normalizer import RcvNormalizer

MANAGER_GROUP = "l10n_cl_dte_core.group_dte_manager"


class RcvCsvImportWizard(models.TransientModel):
    _name = "l10n_cl.dte.rcv.csv.import.wizard"
    _description = "Import RCV CSV"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        domain=lambda self: [("id", "in", self.env.companies.ids)],
    )
    year = fields.Integer(
        required=True, default=lambda self: fields.Date.context_today(self).year
    )
    month = fields.Integer(required=True)
    operation = fields.Selection(DOCUMENT_OPERATIONS, required=True)
    upload = fields.Binary(string="CSV File", required=True, attachment=False)
    filename = fields.Char(required=True)

    def action_import(self):
        """Validate the CSV, store it as a new read and open that read."""
        self.ensure_one()
        self._check_import_allowed()
        content = self._content()
        result = RcvCsvParser(self.env).parse(content)
        sync = self._create_sync(content)
        RcvNormalizer(self.env).apply_csv(sync, result)
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("RCV Read"),
            "res_model": sync._name,
            "res_id": sync.id,
            "view_mode": "form",
            "target": "current",
        }

    def _check_import_allowed(self):
        if not self.env.user.has_group(MANAGER_GROUP):
            raise UserError(self.env._("Only a DTE manager can import an RCV CSV."))
        if self.company_id not in self.env.companies:
            raise UserError(
                self.env._("Select one of the companies you are working in.")
            )
        if not 1 <= self.month <= 12:
            raise UserError(self.env._("The month must be between 1 and 12."))

    def _content(self):
        try:
            return base64.b64decode(self.upload or b"", validate=True)
        except (ValueError, binascii.Error) as error:
            raise UserError(
                self.env._("The uploaded CSV file could not be read.")
            ) from error

    def _period(self):
        period_model = self.env["l10n_cl.dte.rcv.period"]
        values = {
            "company_id": self.company_id.id,
            "year": self.year,
            "month": self.month,
            "operation": self.operation,
        }
        return period_model.search(
            [(key, "=", value) for key, value in values.items()], limit=1
        ) or period_model.create(values)

    def _create_sync(self, content):
        period = self._period()
        now = fields.Datetime.now()
        sync = self.env["l10n_cl.dte.rcv.sync"].create(
            {
                "name": self.env._(
                    "CSV %(period)s · %(file)s",
                    period=period.name,
                    file=self.filename,
                ),
                "period_id": period.id,
                "company_id": self.company_id.id,
                "operation": self.operation,
                "requested_by": self.env.user.id,
                "source": "csv",
                "state": "running",
                "started_at": now,
                "response_hash": hashlib.sha256(content).hexdigest(),
            }
        )
        attachment = self.env["ir.attachment"].create(
            {
                "name": self.filename,
                "type": "binary",
                "raw": content,
                "res_model": sync._name,
                "res_id": sync.id,
                "company_id": self.company_id.id,
            }
        )
        sync.source_file_id = attachment
        return sync
