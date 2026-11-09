# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class CertificationCertificateUploadWizard(models.TransientModel):
    _name = "l10n_cl.dte.certification.certificate.upload.wizard"
    _description = "Upload Certification Certificate"

    set_id = fields.Many2one(
        "l10n_cl.dte.certification.set", required=True, readonly=True
    )
    certificate_file = fields.Binary(
        string="Certificate File (.pfx / .p12)", required=True, attachment=False
    )
    certificate_filename = fields.Char(string="File Name", required=True)
    certificate_password = fields.Char()

    def action_import(self):
        self.ensure_one()
        certification_set = self.set_id
        certification_set._check_signing_upload_access()
        with self.env.cr.savepoint():
            certificate = self.env["certificate.certificate"].create(
                {
                    "name": self.certificate_filename,
                    "company_id": certification_set.company_id.id,
                    "content": self.with_context(bin_size=False).certificate_file,
                    "pkcs12_password": self.certificate_password,
                }
            )
            certification_set.write({"signing_certificate_id": certificate.id})
            self.unlink()
        return {"type": "ir.actions.act_window_close"}
