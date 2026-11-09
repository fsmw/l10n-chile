# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hashlib
import json

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

from ..services.completion_policy import catalogue_completions_allowed
from ..services.receiver_activity_correction import is_receiver_activity_correction
from ..services.receiver_assignment_service import (
    CertificationReceiverAssignmentService,
)
from ..services.set_completeness_service import CertificationSetCompletenessService
from ..services.synthetic_receiver_activity_service import (
    SyntheticReceiverActivityService,
)


class DteCertificationCompletionWizard(models.TransientModel):
    """Prepare existing contacts and review remaining Certification data gaps."""

    _name = "l10n_cl.dte.certification.completion.wizard"
    _description = "Review SII Certification Set Data"
    _check_company_auto = True

    set_id = fields.Many2one(
        "l10n_cl.dte.certification.set",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        related="set_id.company_id", readonly=True, store=True, index=True
    )
    target_mode = fields.Selection(
        [
            ("official_sii", "Official SII Certification"),
            ("local_synthetic", "Local Validation with Synthetic Data"),
        ],
        required=True,
        default="official_sii",
    )
    line_ids = fields.One2many(
        "l10n_cl.dte.certification.completion.line", "wizard_id", copy=False
    )
    case_ids = fields.One2many(related="set_id.case_ids", readonly=True)
    blocking_count = fields.Integer(readonly=True)
    synthetic_count = fields.Integer(readonly=True)
    review_digest = fields.Char(readonly=True)

    @api.model
    def default_get(self, field_list):
        values = super().default_get(field_list)
        set_id = self.env.context.get("default_set_id")
        if set_id:
            values["set_id"] = set_id
        return values

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.action_refresh()
        return records

    def _check_review_access(self):
        self.ensure_one()
        self.set_id._check_certification_access()
        if self.set_id.state not in {"mapping", "ready"} or self.set_id.run_ids:
            raise UserError(
                _("Review is available only before document generation begins.")
            )
        self.set_id._verify_source_integrity()

    def _load_analysis(self):
        self.ensure_one()
        analysis = CertificationSetCompletenessService().analyze_set(
            self.set_id, self.target_mode
        )
        line_model = self.env["l10n_cl.dte.certification.completion.line"]
        self.line_ids.unlink()
        vals_list = []
        sequence = 0
        for case_result in analysis:
            case_id = case_result["case_id"]
            case = self.set_id.case_ids.filtered(
                lambda item, case_id=case_id: item.id == case_id
            )
            for finding in case_result["findings"]:
                sequence += 1
                vals_list.append(
                    {
                        "wizard_id": self.id,
                        "case_id": case.id or False,
                        "case_line_id": finding.get("case_line_id") or False,
                        "sequence": sequence,
                        "diagnostic_code": finding["code"],
                        "diagnostic_message": finding["message"],
                        "target_field": finding["field"],
                        "severity": finding["severity"],
                        "source": finding["source"],
                        "suggested_value": finding.get("proposed_value") or False,
                        "applicable": bool(finding.get("synthetic")),
                    }
                )
        # Set-level configuration diagnostics have no case and remain read-only.
        if vals_list:
            line_model.create(vals_list)
        blocking_count = len(
            [
                item
                for item in analysis
                for finding in item["findings"]
                if finding["severity"] == "blocking"
            ]
        )
        synthetic_count = len(
            [
                item
                for item in analysis
                for finding in item["findings"]
                if finding.get("synthetic")
            ]
        )
        self._write_review_internal(
            {
                "blocking_count": blocking_count,
                "synthetic_count": synthetic_count,
                "review_digest": self._current_review_digest(analysis),
            }
        )
        return analysis

    def _write_review_internal(self, vals):
        return super().write(vals)

    def _current_review_digest(self, analysis=None):
        self.ensure_one()
        if analysis is None:
            analysis = CertificationSetCompletenessService().analyze_set(
                self.set_id, self.target_mode
            )
        payload = {
            "source_digest": self.set_id.source_digest,
            "normalized_digest": self.set_id.normalized_digest,
            "base": CertificationSetCompletenessService().build_review_digest(
                analysis, self.target_mode
            ),
            "decisions": [
                {
                    "case_id": line.case_id.id or False,
                    "code": line.diagnostic_code,
                    "field": line.target_field,
                    "confirmed": line.user_confirmed,
                    "partner_id": line.partner_id.id or False,
                    "product_id": line.product_id.id or False,
                    "user_value_digest": hashlib.sha256(
                        (line.user_value or "").encode("utf-8")
                    ).hexdigest(),
                }
                for line in self.line_ids.sorted("sequence")
            ],
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _check_analysis_current(self):
        self.ensure_one()
        analysis = CertificationSetCompletenessService().analyze_set(
            self.set_id, self.target_mode
        )
        expected = sorted(
            (
                item["case_id"] or False,
                finding.get("case_line_id") or False,
                finding["code"],
                finding["field"],
                finding["source"],
                finding.get("proposed_value") or False,
            )
            for item in analysis
            for finding in item["findings"]
        )
        current = sorted(
            (
                line.case_id.id or False,
                line.case_line_id.id or False,
                line.diagnostic_code,
                line.target_field,
                line.source,
                line.suggested_value or False,
            )
            for line in self.line_ids
        )
        if expected != current:
            raise UserError(_("Refresh the analysis before applying this review."))
        return analysis

    def action_refresh(self):
        """Re-run diagnostics and refresh the proposal list."""
        for wizard in self:
            wizard._check_review_access()
            if wizard.target_mode == "official_sii":
                CertificationReceiverAssignmentService().assign(wizard.set_id)
            wizard._load_analysis()
        return True

    def write(self, vals):
        if not set(vals) <= {"target_mode", "line_ids"}:
            raise UserError(_("Only the review purpose can be changed."))
        for wizard in self:
            wizard._check_review_access()
        for command in vals.get("line_ids", []):
            if (
                not isinstance(command, list | tuple)
                or len(command) != 3
                or command[0] != Command.UPDATE
                or not isinstance(command[2], dict)
                or not set(command[2]) <= {"partner_id", "product_id", "user_confirmed"}
                or any(command[1] not in wizard.line_ids.ids for wizard in self)
            ):
                raise UserError(_("Review findings and proposals cannot be edited."))
        return super().write(vals)

    def action_apply_local_completion(self):
        """Apply only explicitly accepted local synthetic proposals."""
        self.ensure_one()
        self._check_review_access()
        if self.target_mode != "local_synthetic":
            raise UserError(
                _("Select local validation before applying synthetic data.")
            )
        self._check_analysis_current()
        accepted = self.line_ids.filtered(
            lambda line: line.applicable and line.user_confirmed
        )
        if not accepted:
            raise UserError(_("Confirm at least one proposed local completion."))
        if any(
            line.target_field != "sii_reference_reason"
            or line.source != "synthetic_local"
            for line in accepted
        ):
            raise UserError(
                _("This synthetic completion is outside the approved allowlist.")
            )
        with self.env.cr.savepoint():
            generated_activities = False
            for line in accepted:
                value = line.suggested_value
                if not value:
                    raise UserError(_("A confirmed proposal has no value."))
                if is_receiver_activity_correction(line.case_id):
                    expected = SyntheticReceiverActivityService().proposed_text(
                        line.case_id
                    )
                    if not expected or value != expected:
                        raise UserError(
                            _("Refresh the SII activity proposal before applying it.")
                        )
                    if not generated_activities:
                        self.set_id._populate_test_receiver_activities(
                            target_mode=self.target_mode
                        )
                        generated_activities = True
                    continue
                if value != (
                    "Dice: Texto original de prueba. "
                    "Debe decir: Texto corregido de prueba."
                ):
                    raise UserError(
                        _("The local text-correction profile value was changed.")
                    )
                line.case_id._write_completion_values(
                    {"sii_reference_reason": value},
                    {
                        "field": "sii_reference_reason",
                        "origin": "synthetic_local",
                        "profile": "note_text_v1",
                        "value": value,
                    },
                )
            self.set_id._mark_completion_review(
                {
                    "mode": "local_synthetic",
                    "digest": self._current_review_digest(),
                    "contains_synthetic": True,
                }
            )
        return {
            "type": "ir.actions.act_window",
            "res_model": self.set_id._name,
            "res_id": self.set_id.id,
            "view_mode": "form",
        }

    def action_generate_test_receiver_activities(self):
        self.ensure_one()
        self._check_review_access()
        self.set_id._populate_test_receiver_activities(target_mode=self.target_mode)
        return {
            "type": "ir.actions.act_window",
            "res_model": self.set_id._name,
            "res_id": self.set_id.id,
            "view_mode": "form",
        }

    def action_apply_official_review(self):
        """Apply selected existing mappings without per-receiver attestations."""
        self.ensure_one()
        self._check_review_access()
        if self.target_mode != "official_sii":
            raise UserError(
                _("Select official SII certification for reviewed mappings.")
            )
        if (
            self.set_id.contains_synthetic_completion
            and not catalogue_completions_allowed(
                self.set_id, require_official_mode=False
            )
        ):
            raise UserError(
                _(
                    "This set contains generated data outside the approved "
                    "SII catalogue profile."
                )
            )
        self._check_analysis_current()
        confirmed = self.line_ids.filtered(
            lambda line: line.partner_id or line.product_id or line.user_value
        )
        with self.env.cr.savepoint():
            for line in confirmed:
                if line.target_field == "partner_id" and line.partner_id:
                    line.case_id.write({"partner_id": line.partner_id.id})
                elif line.target_field == "product_id" and line.product_id:
                    detail = line.case_line_id
                    if (
                        not detail
                        or detail.case_id != line.case_id
                        or detail.product_id
                    ):
                        raise UserError(
                            _("Select the exact unmapped detail for this product.")
                        )
                    detail.write({"product_id": line.product_id.id})
                elif line.user_value:
                    raise UserError(
                        _("This field cannot be supplied through the official review.")
                    )
            CertificationReceiverAssignmentService().assign(self.set_id)
            self.set_id._mark_completion_review(
                {
                    "mode": "official_sii",
                    "digest": self._current_review_digest(),
                    "contains_synthetic": self.set_id.contains_synthetic_completion,
                }
            )
        return {
            "type": "ir.actions.act_window",
            "res_model": self.set_id._name,
            "res_id": self.set_id.id,
            "view_mode": "form",
        }


class DteCertificationCompletionLine(models.TransientModel):
    """One diagnostic or proposed completion for a certification case."""

    _name = "l10n_cl.dte.certification.completion.line"
    _description = "SII Certification Data Review Line"
    _order = "sequence, id"
    _check_company_auto = True

    wizard_id = fields.Many2one(
        "l10n_cl.dte.certification.completion.wizard",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(
        related="wizard_id.company_id", readonly=True, store=True, index=True
    )
    case_id = fields.Many2one(
        "l10n_cl.dte.certification.case",
        readonly=True,
        ondelete="restrict",
        check_company=True,
    )
    case_line_id = fields.Many2one(
        "l10n_cl.dte.certification.case.line",
        readonly=True,
        ondelete="restrict",
        check_company=True,
    )
    sequence = fields.Integer(required=True)
    diagnostic_code = fields.Char(required=True, readonly=True)
    diagnostic_message = fields.Text(required=True, readonly=True)
    target_field = fields.Char(required=True, readonly=True)
    severity = fields.Selection(
        [
            ("blocking", "Blocking"),
            ("review", "Review Required"),
            ("informational", "Informational"),
        ],
        required=True,
        readonly=True,
    )
    source = fields.Selection(
        [
            ("sii_source", "SII Source File"),
            ("company_configuration", "Company Configuration"),
            ("existing_odoo_record", "Existing Odoo Record"),
            ("synthetic_local", "Synthetic Local Data"),
            ("user_input", "User Input"),
        ],
        required=True,
        readonly=True,
    )
    suggested_value = fields.Char(readonly=True)
    user_value = fields.Text()
    partner_id = fields.Many2one("res.partner", check_company=True)
    product_id = fields.Many2one("product.product", check_company=True)
    user_confirmed = fields.Boolean(default=False)
    applicable = fields.Boolean(readonly=True)

    def write(self, vals):
        allowed = {"user_confirmed", "partner_id", "product_id", "user_value"}
        if not set(vals) <= allowed:
            raise UserError(_("Review findings and proposals cannot be edited."))
        for line in self:
            line.wizard_id._check_review_access()
            if "partner_id" in vals and line.target_field != "partner_id":
                raise UserError(
                    _("A receiver can only be assigned to a receiver finding.")
                )
            if "product_id" in vals and line.target_field != "product_id":
                raise UserError(
                    _("A product can only be assigned to a product finding.")
                )
            if "user_value" in vals:
                raise UserError(_("Manual values are not enabled for this review."))
        return super().write(vals)
