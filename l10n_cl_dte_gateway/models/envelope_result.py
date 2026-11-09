# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..services.envelope_result_parser import reconcile_result


class SiiEnvelopeResultImport(models.Model):
    _name = "l10n_cl.sii.envelope.result.import"
    _description = "SII Envelope Result Import"
    _order = "imported_at desc, id desc"

    submission_id = fields.Many2one(
        "l10n_cl.sii.envelope.submission",
        required=True,
        index=True,
        ondelete="restrict",
    )
    company_id = fields.Many2one(
        related="submission_id.company_id", store=True, index=True
    )
    attempt_id = fields.Many2one(
        "l10n_cl.sii.envelope.attempt",
        required=True,
        index=True,
        ondelete="restrict",
    )
    imported_at = fields.Datetime(required=True, readonly=True)
    imported_by_id = fields.Many2one("res.users", required=True, readonly=True)
    source_digest = fields.Char(required=True, readonly=True, index=True)
    source_filename = fields.Char(required=True, readonly=True)
    source_xml = fields.Binary(related="attempt_id.response_xml", readonly=True)
    source_xml_filename = fields.Char(
        related="attempt_id.response_filename", readonly=True
    )
    verification_state = fields.Selection(
        [("operator_attestation", "Operator supplied")],
        required=True,
        readonly=True,
        default="operator_attestation",
    )
    reconciliation_state = fields.Selection(
        [("reconciled", "Reconciled"), ("mismatch", "Needs review")],
        required=True,
        readonly=True,
    )
    sii_state = fields.Char(readonly=True)
    expected_count = fields.Integer(readonly=True)
    accepted_count = fields.Integer(readonly=True)
    rejected_count = fields.Integer(readonly=True)
    repair_count = fields.Integer(readonly=True)
    indeterminate_count = fields.Integer(readonly=True)
    discrepancy_summary = fields.Text(readonly=True)
    line_ids = fields.One2many("l10n_cl.sii.envelope.result.line", "import_id")

    _sql_constraints = [
        (
            "envelope_result_digest_uniq",
            "unique(submission_id, source_digest)",
            "This SII result file was already imported for the envelope.",
        )
    ]

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super
        raise UserError(_("SII result imports are created from the import wizard."))

    @api.model
    def _create_from_wizard(self, values, line_values):
        result = super(SiiEnvelopeResultImport, self.sudo()).create(values)
        for line in line_values:
            self.env["l10n_cl.sii.envelope.result.line"]._create_from_wizard(
                {**line, "import_id": result.id}
            )
        return result

    def write(self, values):  # pylint: disable=method-required-super
        raise UserError(_("Imported SII result evidence is immutable."))

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("Imported SII result evidence is retained."))

    def action_open(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Imported SII Result"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }


class SiiEnvelopeResultLine(models.Model):
    _name = "l10n_cl.sii.envelope.result.line"
    _description = "SII Envelope Document Result"
    _order = "document_type, folio"

    import_id = fields.Many2one(
        "l10n_cl.sii.envelope.result.import",
        required=True,
        index=True,
        ondelete="restrict",
    )
    submission_id = fields.Many2one(
        related="import_id.submission_id", store=True, index=True
    )
    company_id = fields.Many2one(related="import_id.company_id", store=True, index=True)
    issuer_vat = fields.Char(required=True, readonly=True)
    receiver_vat = fields.Char(readonly=True)
    document_type = fields.Char(required=True, readonly=True)
    folio = fields.Char(required=True, readonly=True)
    issue_date = fields.Char(readonly=True)
    document_digest = fields.Char(required=True, readonly=True)
    state = fields.Selection(
        [
            ("accepted", "Reported accepted"),
            ("accepted_with_observations", "Reported with observations"),
            ("rejected", "Reported rejected"),
            ("indeterminate", "Indeterminate"),
        ],
        required=True,
        readonly=True,
    )
    sii_state_code = fields.Char(readonly=True)
    sii_detail = fields.Text(readonly=True)

    _sql_constraints = [
        (
            "envelope_result_identity_uniq",
            "unique(import_id, document_type, folio)",
            "An imported result cannot repeat a DTE identity.",
        )
    ]

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super
        raise UserError(_("SII result lines are created from the import wizard."))

    @api.model
    def _create_from_wizard(self, values):
        return super(SiiEnvelopeResultLine, self.sudo()).create(values)

    def write(self, values):  # pylint: disable=method-required-super
        raise UserError(_("Imported SII document results are immutable."))

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("Imported SII document results are retained."))


class SiiEnvelopeResultImportWizard(models.TransientModel):
    _name = "l10n_cl.sii.envelope.result.import.wizard"
    _description = "Import SII Envelope Result"

    submission_id = fields.Many2one(
        "l10n_cl.sii.envelope.submission", required=True, readonly=True
    )
    result_file = fields.Binary(required=True)
    result_filename = fields.Char(required=True)

    def action_import(self):
        self.ensure_one()
        submission = self.submission_id
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise UserError(_("Only a DTE manager can import an SII result."))
        if submission.environment != "certification":
            raise UserError(_("Manual result import is limited to Certification."))
        if (
            submission.state
            not in {
                "submitted",
                "processing",
                "result_pending",
                "accepted",
                "accepted_with_observations",
                "rejected",
            }
            or not submission.track_id
        ):
            raise UserError(_("A submitted envelope with a TrackID is required."))
        try:
            response = base64.b64decode(self.result_file, validate=True)
        except (TypeError, ValueError) as error:
            raise ValidationError(_("Upload a valid SII result XML.")) from error
        digest = hashlib.sha256(response).hexdigest()
        result_model = self.env["l10n_cl.sii.envelope.result.import"]
        existing = result_model.search(
            [
                ("submission_id", "=", submission.id),
                ("source_digest", "=", digest),
            ],
            limit=1,
        )
        if existing:
            return existing.action_open()
        parsed = reconcile_result(
            response,
            submission._verify_request(),
            submission.company_id.vat,
            submission.track_id,
        )
        attempt = self.env["l10n_cl.sii.envelope.attempt"]._create_for_operation(
            submission, "result_import", request_digest=digest
        )
        summary = "; ".join(parsed["discrepancies"]) or False
        attempt._complete(
            "completed",
            response,
            error_summary=summary,
            response_filename=self.result_filename,
        )
        imported = result_model._create_from_wizard(
            {
                "submission_id": submission.id,
                "attempt_id": attempt.id,
                "imported_at": fields.Datetime.now(),
                "imported_by_id": self.env.user.id,
                "source_digest": digest,
                "source_filename": self.result_filename,
                "verification_state": "operator_attestation",
                "reconciliation_state": parsed["reconciliation_state"],
                "sii_state": parsed["sii_state"],
                "expected_count": parsed["expected_count"],
                "accepted_count": parsed["accepted_count"],
                "rejected_count": parsed["rejected_count"],
                "repair_count": parsed["repair_count"],
                "indeterminate_count": parsed["indeterminate_count"],
                "discrepancy_summary": summary,
            },
            parsed["line_values"],
        )
        # This is an operator-supplied copy of SII evidence. Importing it does
        # not set or overwrite the fiscal state of the envelope.
        return imported.action_open()
