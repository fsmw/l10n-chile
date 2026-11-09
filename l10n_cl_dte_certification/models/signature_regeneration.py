# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class CertificationSignatureRegeneration(models.Model):
    _name = "l10n_cl.dte.certification.signature.regeneration"
    _description = "Certification Signature Regeneration"
    _order = "create_date desc, id desc"
    _check_company_auto = True
    _rec_name = "run_id"

    run_id = fields.Many2one(
        "l10n_cl.dte.certification.run",
        required=True,
        ondelete="restrict",
        check_company=True,
        index=True,
    )
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    actor_id = fields.Many2one("res.users", required=True, ondelete="restrict")
    certificate_id = fields.Many2one(
        "certificate.certificate",
        required=True,
        ondelete="restrict",
        check_company=True,
    )
    certificate_fingerprint = fields.Char(required=True)
    certificate_name = fields.Char(required=True)
    previous_envelope_file = fields.Binary(
        attachment=True,
        copy=False,
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    previous_envelope_filename = fields.Char()
    previous_envelope_digest = fields.Char()
    replacement_envelope_file = fields.Binary(
        attachment=True,
        required=True,
        copy=False,
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    replacement_envelope_filename = fields.Char(required=True)
    replacement_envelope_digest = fields.Char(required=True)
    signature_changes = fields.Json(required=True)

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super
        raise AccessError(
            _("Signature history is recorded by the regeneration action.")
        )

    @api.model
    def _record(self, run, certificate, previous, changes):
        run._check_execution_access()
        return super().create(
            {
                "run_id": run.id,
                "actor_id": self.env.uid,
                "certificate_id": certificate.id,
                "certificate_name": certificate.display_name,
                "certificate_fingerprint": certificate._get_fingerprint_bytes(
                    hashing_algorithm="sha256", formatting="raw"
                ).hex(),
                "previous_envelope_file": previous["file"],
                "previous_envelope_filename": previous["filename"],
                "previous_envelope_digest": previous["digest"],
                "replacement_envelope_file": run.envelope_file,
                "replacement_envelope_filename": run.envelope_filename,
                "replacement_envelope_digest": run.envelope_digest,
                "signature_changes": changes,
            }
        )

    def write(self, vals):  # pylint: disable=method-required-super
        raise AccessError(_("Signature regeneration history is immutable."))

    def unlink(self):  # pylint: disable=method-required-super
        raise AccessError(_("Signature regeneration history cannot be deleted."))
