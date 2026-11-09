# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class DteCertificationReceiverConfirmationWizard(models.TransientModel):
    _name = "l10n_cl.dte.certification.receiver.confirmation.wizard"
    _description = "Confirm SII Certification Case Receiver"

    case_id = fields.Many2one(
        "l10n_cl.dte.certification.case", required=True, ondelete="cascade"
    )
    partner_id = fields.Many2one(related="case_id.partner_id", readonly=True)
    classification = fields.Selection(
        [
            ("existing_customer", "Existing External Customer"),
            ("synthetic_uat", "Synthetic Local Test Data"),
        ],
        required=True,
    )
    verification_reference = fields.Char()
    attestation_notes = fields.Text()

    def action_apply(self):
        """Persist receiver classification, evidence reference and reviewer."""
        self.ensure_one()
        case = self.case_id
        case.set_id._check_certification_access()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(_("Only a DTE manager can attest a case receiver."))
        if case.set_id.state != "ready" or not case.set_id.can_edit_envelope:
            raise UserError(_("Receiver attestation is frozen after execution starts."))
        partner = case.partner_id
        if not partner or not partner.vat:
            raise ValidationError(_("Select a receiver with a Chilean RUT."))
        caf_model = self.env["l10n_cl.caf.authorization"]
        normalized_vat = caf_model._normalize_vat(partner.vat)
        if not caf_model._is_valid_vat(normalized_vat):
            raise ValidationError(_("The receiver RUT is invalid."))
        if self.classification == "existing_customer":
            if partner == case.set_id.company_id.partner_id:
                raise ValidationError(_("The issuer cannot be its own receiver."))
            if not (self.verification_reference or "").strip():
                raise ValidationError(
                    _("Record the source used to verify the external customer.")
                )
        elif self.classification != "synthetic_uat":
            raise ValidationError(_("Select a supported receiver classification."))
        case._write_internal(
            {
                "receiver_classification": self.classification,
                "receiver_verification_reference": (
                    self.verification_reference.strip()
                    if self.classification == "existing_customer"
                    else False
                ),
                "receiver_verified_at": fields.Datetime.now(),
                "receiver_verified_by": self.env.user.id,
            }
        )
        process = self.env["l10n_cl.dte.certification.process"].search(
            [("certification_set_ids", "in", case.set_id.id)], limit=1
        )
        if process:
            process._record_event(
                "receiver_attested",
                details={
                    "case": case.case_identifier,
                    "classification": self.classification,
                    "reviewer_id": self.env.user.id,
                },
            )
        return {"type": "ir.actions.act_window_close"}
