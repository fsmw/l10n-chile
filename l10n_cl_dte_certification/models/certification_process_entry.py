# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Entry-requirement checks of a certification process."""

from odoo import _, models
from odoo.exceptions import UserError, ValidationError


class DteCertificationProcessEntry(models.AbstractModel):
    """Entry evidence required by the taxpayer's certification path."""

    _name = "l10n_cl.dte.certification.process.entry"
    _description = "SII DTE Certification Entry Requirements"

    def _check_certification_entry(self):
        """Require evidence appropriate to this taxpayer's certification path."""
        self.ensure_one()
        if self.process_mode == "assigned_test_set":
            return self._validate_assigned_test_set_entry()
        requirements = self._entry_requirements()
        code, evidence_type = (
            ("accepted_postulation", "postulation_acceptance")
            if self.process_mode == "new_postulation"
            else ("existing_authorization", "existing_issuer_authorization")
        )
        requirement = requirements.filtered(lambda item: item.code == code)
        if (
            len(requirement) != 1
            or requirement.state != "accepted"
            or not requirement.evidence_ids.filtered(
                lambda item: (
                    item.evidence_type == evidence_type
                    and item.origin == "sii_portal"
                    and item.verification_state == "source_verified"
                    and (
                        not self.sii_application_reference
                        or item.external_reference == self.sii_application_reference
                    )
                )
            )
        ):
            raise UserError(self._entry_gate_error())
        return requirement

    def _validate_assigned_test_set_entry(self):
        """Require an SII-assigned set and accepted envelope to start its cycle."""
        self.ensure_one()
        assigned_stage = self.stage_ids.filtered(
            lambda item: item.code == "assigned_set"
        )
        if len(assigned_stage) != 1 or not assigned_stage.certification_set_id:
            raise UserError(
                _("Link the SII-assigned test set before starting this certification.")
            )
        certification_set = assigned_stage.certification_set_id
        run, submission = certification_set._get_sii_advance_result()
        if (
            not run
            or run not in assigned_stage.certification_run_ids
            or not submission
            or submission not in assigned_stage.sii_envelope_submission_ids
        ):
            raise UserError(
                _(
                    "The complete assigned set must have an accepted SII envelope. "
                    "Record its official SETMAIL result in the Assigned Test Set "
                    "stage."
                )
            )
        return assigned_stage

    def _entry_gate_error(self):
        self.ensure_one()
        if self.process_mode == "authorized_taxpayer":
            return _(
                "Record source evidence of the taxpayer's existing SII "
                "authorization before starting certification."
            )
        return _(
            "Record the SII-accepted postulation and its portal evidence "
            "before starting certification."
        )

    def _entry_requirements(self):
        self.ensure_one()
        stage = self.stage_ids.filtered(lambda item: item.code == "assigned_set")
        if len(stage) != 1:
            raise ValidationError(_("The assigned-set stage is missing."))
        requirements = stage.requirement_ids.filtered(
            lambda item: item.code in {"accepted_postulation", "existing_authorization"}
        )
        created_requirement = False
        for code in ("accepted_postulation", "existing_authorization"):
            if any(item.code == code for item in requirements):
                continue
            template = (
                self.env["l10n_cl.dte.certification.requirement.template"]
                .sudo()
                .search(
                    [
                        ("stage_code", "=", "assigned_set"),
                        ("code", "=", code),
                        ("active", "=", True),
                    ],
                    order="normative_version desc, id desc",
                    limit=1,
                )
            )
            if not template:
                raise ValidationError(
                    _("The active certification entry requirement %s is missing.")
                    % code
                )
            created = self.env[
                "l10n_cl.dte.certification.process.requirement"
            ]._create_internal(
                [
                    {
                        "stage_id": stage.id,
                        "template_id": template.id,
                        "code": template.code,
                        "name": template.name,
                        "sequence": template.sequence,
                        "mandatory": template.mandatory,
                        "external_result": template.external_result,
                        "applicability": template.applicability,
                        "normative_title": template.normative_title,
                        "normative_url": template.normative_url,
                        "normative_version": template.normative_version,
                        "normative_sha256": template.normative_sha256,
                    }
                ]
            )
            requirements |= created
            created_requirement = True
        if created_requirement:
            self._apply_entry_requirement_mode(requirements)
        if len(requirements) != 2:
            raise ValidationError(
                _("Both certification entry requirements must be configured.")
            )
        return requirements

    def _configure_entry_requirements(self):
        """Make the applicable authorization gate mandatory and the other N/A."""
        for process in self:
            requirements = process._entry_requirements()
            process._apply_entry_requirement_mode(requirements)

    def _apply_entry_requirement_mode(self, requirements):
        """Apply this process mode to the two authorization checklist lines."""
        for process in self:
            if len(requirements) != 2:
                raise ValidationError(
                    _("Both certification entry requirements must be configured.")
                )
            required_code = {
                "new_postulation": "accepted_postulation",
                "authorized_taxpayer": "existing_authorization",
            }.get(process.process_mode)
            for requirement in requirements:
                if required_code and requirement.code == required_code:
                    requirement._write_internal(
                        {
                            "mandatory": True,
                            "applicability": "required",
                            "state": (
                                "pending"
                                if requirement.state == "not_applicable"
                                else requirement.state
                            ),
                            "applicability_reason": False,
                        }
                    )
                else:
                    requirement._write_internal(
                        {
                            "mandatory": False,
                            "applicability": "not_applicable",
                            "state": "not_applicable",
                            "applicability_reason": _(
                                "Not applicable for the selected certification "
                                "entry mode."
                            ),
                        }
                    )
