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
    # Never stored: the inverse hands the password straight to
    # certificate.certificate while the dialog is saved, so no copy is left in
    # the transient table, even when the upload later fails.
    certificate_password = fields.Char(
        compute="_compute_certificate_password",
        inverse="_inverse_certificate_password",
        store=False,
    )
    certificate_id = fields.Many2one(
        "certificate.certificate", readonly=True, copy=False
    )

    def _compute_certificate_password(self):
        self.certificate_password = False

    def _inverse_certificate_password(self):
        for wizard in self.filtered("certificate_password"):
            wizard.certificate_id = wizard._create_certificate(
                wizard.certificate_password
            )

    def _create_certificate(self, password):
        self.ensure_one()
        self.set_id._check_signing_upload_access()
        return self.env["certificate.certificate"].create(
            {
                "name": self.certificate_filename,
                "company_id": self.set_id.company_id.id,
                "content": self.with_context(bin_size=False).certificate_file,
                "pkcs12_password": password or False,
            }
        )

    def action_import(self):
        self.ensure_one()
        certification_set = self.set_id
        certification_set._check_signing_upload_access()
        with self.env.cr.savepoint():
            certificate = self.certificate_id or self._create_certificate(False)
            certification_set.write({"signing_certificate_id": certificate.id})
            self.unlink()
        return {"type": "ir.actions.act_window_close"}
