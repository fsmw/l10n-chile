# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Transmission of a certification envelope to the SII."""

import base64
import binascii
import hashlib

from odoo import _, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.l10n_cl_dte_gateway.services.envelope_composer import (
    SiiEnvelopeComposer,
)
from odoo.addons.l10n_cl_dte_signing.services.certificate_identity import normalize_rut
from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService


class DteCertificationRunSubmission(models.AbstractModel):
    """Send a prepared certification envelope and open its SII submission."""

    _name = "l10n_cl.dte.certification.run.submission"
    _description = "SII DTE Certification Run Submission"

    def action_send_certification_envelope(self):
        """Create a durable transmission after an explicit operator action."""
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise UserError(_("Only DTE managers can submit certification envelopes."))
        if self.company_id != self.env.company:
            raise UserError(_("Use the execution company as the active company."))
        if self.company_id.dte_sii_environment != "certification":
            raise UserError(
                _("SII certification submission requires Certification mode.")
            )
        self.env.cr.execute(
            "SELECT id FROM l10n_cl_dte_certification_run WHERE id = %s FOR UPDATE",
            [self.id],
        )
        self.invalidate_recordset(
            ["envelope_xml", "envelope_digest", "sii_submission_id"]
        )
        if self.sii_submission_id:
            if (
                self.sii_submission_id.company_id != self.company_id
                or self.sii_submission_id.environment != "certification"
                or self.sii_submission_id.request_digest != self.envelope_digest
            ):
                raise ValidationError(
                    _("The linked SII transmission does not match this envelope.")
                )
            if self.sii_submission_id.state == "pending":
                return self.sii_submission_id.action_enqueue_send()
            return self.sii_submission_id.action_open()
        if self.state != "prepared" or not self.envelope_xml:
            raise UserError(
                _("Export the complete signed envelope before submitting it.")
            )

        try:
            content = base64.b64decode(self.envelope_xml, validate=True)
        except (binascii.Error, TypeError, ValueError) as error:
            raise ValidationError(
                _("The stored envelope is not valid base64 content.")
            ) from error
        digest = hashlib.sha256(content).hexdigest()
        if digest != self.envelope_digest:
            raise ValidationError(_("The stored envelope failed its integrity check."))
        for line in self.line_ids.sorted("sequence"):
            line._validate_envelope_case_result()
        SiiEnvelopeComposer().validate(
            content,
            expected_count=len(self.line_ids),
            signature_service=SignatureService(),
        )
        sender_vat = SiiEnvelopeComposer().get_sender_vat(content)
        self.env["l10n_cl.sii.gateway.factory"]._get_ready_envelope_gateway(
            self.company_id, sender_vat, "certification"
        )

        submission_key = hashlib.sha256(
            f"certification-run:{self.id}:{self.revision}:{digest}".encode()
        ).hexdigest()
        submission_model = self.env["l10n_cl.sii.envelope.submission"]
        submission = submission_model.search(
            [("idempotency_key", "=", submission_key)], limit=1
        )
        if not submission:
            submission = submission_model._create_from_workflow(
                {
                    "name": f"{self.name} - SII envelope",
                    "company_id": self.company_id.id,
                    "environment": "certification",
                    "request_xml": base64.b64encode(content),
                    "request_filename": self.envelope_filename,
                    "request_digest": digest,
                    "idempotency_key": submission_key,
                }
            )
        self._record_sii_submission(submission)
        return submission.action_enqueue_send()

    def action_open_sii_submission(self):
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise UserError(_("Only DTE managers can open SII transmission details."))
        if not self.sii_submission_id:
            raise UserError(_("This execution has no SII transmission record yet."))
        return self.sii_submission_id.action_open()

    def action_reconcile_sii_result(self):
        """Link the latest structurally reconciled gateway result to frozen cases."""
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise UserError(_("Only DTE managers can reconcile SII results."))
        if self.company_id != self.env.company:
            raise UserError(_("Use the execution company as the active company."))
        self.env.cr.execute(
            "SELECT id FROM l10n_cl_dte_certification_run WHERE id = %s FOR UPDATE",
            [self.id],
        )
        self.invalidate_recordset(["sii_submission_id", "result_reconciliation_ids"])
        submission = self.sii_submission_id
        if not submission or submission.environment != "certification":
            raise UserError(_("This execution has no Certification transmission."))
        if not submission.track_id:
            raise UserError(_("The SII transmission needs a TrackID first."))
        if (
            submission.company_id != self.company_id
            or submission.request_digest != self.envelope_digest
        ):
            raise ValidationError(
                _("The SII transmission does not match this execution envelope.")
            )
        imported = submission.result_import_ids.sorted(
            key=lambda item: (item.imported_at, item.id), reverse=True
        )[:1]
        if not imported:
            raise UserError(
                _("Import the SII result XML from the transmission before reconciling.")
            )
        if imported.reconciliation_state != "reconciled":
            raise UserError(
                _("The imported SII result has discrepancies and needs review first.")
            )
        existing = self.env["l10n_cl.dte.certification.run.result"].search(
            [("run_id", "=", self.id), ("source_digest", "=", imported.source_digest)],
            limit=1,
        )
        if existing:
            return existing.action_open()

        case_by_identity = {}
        for run_line in self.line_ids:
            if not run_line.document_id:
                raise ValidationError(
                    _("Prepare every frozen case before reconciling the SII result.")
                )
            identity = (
                run_line.document_code,
                str(run_line.document_id.folio),
            )
            if identity in case_by_identity:
                raise ValidationError(
                    _("The execution repeats a DTE type and folio identity.")
                )
            case_by_identity[identity] = run_line
        if imported.expected_count != len(case_by_identity) or len(
            imported.line_ids
        ) != len(case_by_identity):
            raise ValidationError(
                _("The imported result does not cover every frozen execution case.")
            )

        result_line_values = []
        matched = set()
        for imported_line in imported.line_ids:
            identity = (imported_line.document_type, imported_line.folio)
            run_line = case_by_identity.get(identity)
            if not run_line or identity in matched:
                raise ValidationError(
                    _("The imported result contains an unknown or duplicate case.")
                )
            expected_issuer = normalize_rut(self.company_id.partner_id.vat)
            expected_receiver = normalize_rut(run_line.partner_id.vat)
            if (
                normalize_rut(imported_line.issuer_vat) != expected_issuer
                or normalize_rut(imported_line.receiver_vat) != expected_receiver
            ):
                raise ValidationError(
                    _("The imported result identity differs from the frozen case.")
                )
            matched.add(identity)
            result_line_values.append(
                {
                    "run_line_id": run_line.id,
                    "gateway_result_line_id": imported_line.id,
                    "document_type": identity[0],
                    "folio": identity[1],
                    "state": imported_line.state,
                    "sii_state_code": imported_line.sii_state_code,
                    "sii_detail": imported_line.sii_detail,
                }
            )
        result = self.env["l10n_cl.dte.certification.run.result"]._create_from_import(
            {
                "run_id": self.id,
                "submission_id": submission.id,
                "result_import_id": imported.id,
                "source_digest": imported.source_digest,
                "reconciled_at": fields.Datetime.now(),
                "reconciled_by_id": self.env.user.id,
                "sii_state": imported.sii_state,
                "expected_count": imported.expected_count,
                "accepted_count": imported.accepted_count,
                "rejected_count": imported.rejected_count,
                "repair_count": imported.repair_count,
                "indeterminate_count": imported.indeterminate_count,
            },
            result_line_values,
        )
        return result.action_open()
