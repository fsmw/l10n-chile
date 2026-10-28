# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import base64
import csv
import hashlib
import io

from odoo import _, fields, models
from odoo.exceptions import UserError


class RcvCsvWizard(models.TransientModel):
    _name = "l10n.cl.rcv.csv.wizard"
    _description = "Import RCV CSV"

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    year = fields.Integer(required=True)
    month = fields.Integer(required=True)
    operation = fields.Selection(
        [("purchase", "Compras"), ("sale", "Ventas")], required=True
    )
    upload = fields.Binary(required=True)
    filename = fields.Char(required=True)

    def action_import(self):
        self.ensure_one()
        try:
            content = base64.b64decode(self.upload)
            if len(content) > 10 * 1024 * 1024:
                raise UserError(_("El CSV no puede superar 10 MB."))
            text = content.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text))
        except (ValueError, UnicodeDecodeError) as exc:
            raise UserError(
                _("El archivo CSV debe estar codificado en UTF-8.")
            ) from exc
        required = {
            "tipo_documento",
            "rut_contraparte",
            "folio",
            "fecha",
            "neto",
            "exento",
            "iva",
            "total",
        }
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise UserError(_("El CSV debe contener: %s") % ", ".join(sorted(required)))
        period = self.env["l10n.cl.rcv.period"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("year", "=", self.year),
                ("month", "=", self.month),
                ("operation", "=", self.operation),
            ],
            limit=1,
        ) or self.env["l10n.cl.rcv.period"].create(
            {
                "company_id": self.company_id.id,
                "year": self.year,
                "month": self.month,
                "operation": self.operation,
            }
        )
        run = self.env["l10n.cl.rcv.sync"].create(
            {
                "period_id": period.id,
                "company_id": self.company_id.id,
                "operation": self.operation,
                "requested_by": self.env.user.id,
                "source": "csv",
                "state": "running",
                "started_at": fields.Datetime.now(),
            }
        )
        attachment = self.env["ir.attachment"].create(
            {
                "name": self.filename,
                "type": "binary",
                "datas": self.upload,
                "res_model": run._name,
                "res_id": run.id,
                "company_id": self.company_id.id,
            }
        )
        run.write(
            {
                "source_file_id": attachment.id,
                "response_hash": hashlib.sha256(content).hexdigest(),
            }
        )
        return self.env["l10n.cl.rcv.normalizer"]._apply_csv(run, list(reader))
