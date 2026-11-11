# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import binascii
import hashlib
import json

from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.l10n_cl_dte_core.tools.xml_safety import safe_fromstring
from odoo.addons.l10n_cl_dte_gateway.services.envelope_composer import (
    SiiEnvelopeComposer,
)
from odoo.addons.l10n_cl_dte_signing.services.certificate_identity import normalize_rut
from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService

from ..services.access import check_certification_access


def _canonical_digest(value):
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class DteCertificationRun(models.Model):
    _name = "l10n_cl.dte.certification.run"
    _description = "SII Certification Execution Snapshot"
    _order = "set_id, revision desc"
    _check_company_auto = True
    _rec_name = "name"

    name = fields.Char(required=True, readonly=True, index=True)
    set_id = fields.Many2one(
        "l10n_cl.dte.certification.set",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    company_id = fields.Many2one(
        related="set_id.company_id", store=True, readonly=True, index=True
    )
    revision = fields.Integer(required=True, readonly=True)
    emission_date = fields.Date(required=True, readonly=True)
    created_by = fields.Many2one(
        "res.users", required=True, readonly=True, default=lambda self: self.env.user
    )
    set_digest = fields.Char(required=True, readonly=True, index=True)
    snapshot_digest = fields.Char(required=True, readonly=True, index=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("generated", "Documents Generated"),
            ("prepared", "DTE Prepared"),
            ("partial", "Partially Generated"),
            ("blocked", "Blocked"),
        ],
        required=True,
        readonly=True,
        default="draft",
    )
    line_ids = fields.One2many(
        "l10n_cl.dte.certification.run.line",
        "run_id",
        readonly=True,
        copy=False,
    )
    line_count = fields.Integer(compute="_compute_line_count")
    can_operate = fields.Boolean(compute="_compute_can_operate")
    envelope_xml = fields.Binary(
        string="Certification Envelope",
        attachment=True,
        readonly=True,
        copy=False,
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    envelope_filename = fields.Char(readonly=True, copy=False)
    envelope_digest = fields.Char(readonly=True, copy=False, index=True)
    envelope_built_at = fields.Datetime(readonly=True, copy=False)
    sii_submission_id = fields.Many2one(
        "l10n_cl.sii.envelope.submission",
        string="SII Envelope Transmission",
        readonly=True,
        copy=False,
        ondelete="restrict",
        check_company=True,
    )
    result_reconciliation_ids = fields.One2many(
        "l10n_cl.dte.certification.run.result",
        "run_id",
        readonly=True,
        copy=False,
    )

    @api.depends_context("uid", "company")
    def _compute_can_operate(self):
        company = self.env.company
        can_operate = (
            self.env.user.has_group("l10n_cl_dte_core.group_dte_manager")
            and company in self.env.companies
            and company.dte_sii_environment == "certification"
        )
        for run in self:
            run.can_operate = bool(can_operate and run.company_id == company)

    def _check_execution_access(self):
        self.ensure_one()
        check_certification_access(self.env, self.company_id)

    def action_open_generation_wizard(self):
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise UserError(
                _("Only DTE managers can generate certification documents.")
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Generate Certification Drafts"),
            "res_model": "l10n_cl.dte.certification.generation.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_run_id": self.id},
        }

    def action_prepare_dte(self):
        """Prepare signed DTEs offline for generated certification cases."""
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise UserError(_("Only DTE managers can prepare certification DTEs."))
        if self.company_id != self.env.company:
            raise UserError(_("Use the execution company as the active company."))
        if self.company_id.dte_sii_environment != "certification":
            raise UserError(_("Certification DTEs require Certification mode."))
        if self.state not in {"generated", "partial", "prepared"}:
            raise UserError(_("Generate the execution documents first."))

        from odoo.addons.l10n_cl_dte_account.services.dte_issuance_service import (
            DteIssuanceService,
        )

        for line in self.line_ids.sorted("sequence"):
            if line.generation_state != "generated" or line.document_id:
                continue
            try:
                with self.env.cr.savepoint():
                    line._check_preparation_source()
                    if line.document_code in {"56", "61"}:
                        line._link_certification_note_target()
                    move = line.move_id.with_user(self.env.user)
                    adapter = move._get_dte_issuance_adapter(line.document_code)
                    document = DteIssuanceService().prepare(
                        move, adapter, line.document_code
                    )
                    line._record_preparation_result(document, "prepared")
            except (UserError, ValidationError) as error:
                line._record_preparation_result(False, "failed", str(error))

        prepared = self.line_ids.filtered(
            lambda line: (
                line.generation_state == "generated"
                and line.preparation_state == "prepared"
            )
        )
        failed = self.line_ids.filtered(
            lambda line: (
                line.generation_state == "generated"
                and line.preparation_state == "failed"
            )
        )
        generated = self.line_ids.filtered(
            lambda line: line.generation_state == "generated"
        )
        state = (
            "prepared"
            if generated
            and len(generated) == len(self.line_ids)
            and len(prepared) == len(generated)
            else "partial"
            if prepared or failed
            else "blocked"
        )
        self._record_generation_state(state)
        return self.action_open()

    def action_export_envelope(self):
        """Build and download a validated envelope without contacting the SII."""
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise UserError(_("Only DTE managers can export certification envelopes."))
        if self.company_id != self.env.company:
            raise UserError(_("Use the execution company as the active company."))
        if self.company_id.dte_sii_environment != "certification":
            raise UserError(_("Certification envelopes require Certification mode."))
        self.env.cr.execute(
            "SELECT id FROM l10n_cl_dte_certification_run WHERE id = %s FOR UPDATE",
            [self.id],
        )
        self.invalidate_recordset(
            ["envelope_xml", "envelope_filename", "envelope_digest"]
        )
        if self.envelope_xml:
            try:
                stored_envelope = base64.b64decode(self.envelope_xml, validate=True)
            except (binascii.Error, TypeError, ValueError) as error:
                raise ValidationError(
                    _("The stored certification envelope is not valid base64 content.")
                ) from error
            if hashlib.sha256(stored_envelope).hexdigest() != self.envelope_digest:
                raise ValidationError(
                    _("The stored certification envelope failed its integrity check.")
                )
            return self._download_envelope()
        if self.state != "prepared" or not self.line_ids:
            raise UserError(_("Prepare every DTE before exporting the envelope."))

        documents = []
        for line in self.line_ids.sorted("sequence"):
            line._validate_envelope_case_result()
            artifact = line.document_id._get_signed_artifact()
            artifact.action_verify_integrity()
            try:
                content = base64.b64decode(artifact.content, validate=True)
            except (binascii.Error, TypeError, ValueError) as error:
                raise ValidationError(
                    _("The signed DTE evidence is not valid base64 content.")
                ) from error
            documents.append(content)

        signature_service = SignatureService()
        _certificate, sender_vat = signature_service.certification_signing_identity(
            self.company_id
        )
        run_key = hashlib.sha256(
            f"{self.set_id.id}:{self.revision}:{self.snapshot_digest}".encode()
        ).hexdigest()[:24]
        envelope = SiiEnvelopeComposer().compose(
            self.company_id,
            documents,
            {
                "id": f"Set-{run_key}",
                "sender_vat": sender_vat,
                "resolution_number": self.company_id.dte_sii_resolution_number,
                "resolution_date": self.company_id.dte_sii_resolution_date,
            },
            signature_service=signature_service,
        )
        self._record_exported_envelope(envelope)
        return self._download_envelope()

    def _record_exported_envelope(self, envelope):
        self.ensure_one()
        if self.envelope_xml:
            raise ValidationError(_("The execution already has an exported envelope."))
        attention_number = self.line_ids.sorted("sequence")[0].case_id.attention_number
        return super(DteCertificationRun, self.sudo()).write(
            {
                "envelope_xml": base64.b64encode(envelope),
                "envelope_filename": f"SET-{attention_number}-{self.revision}.xml",
                "envelope_digest": hashlib.sha256(envelope).hexdigest(),
                "envelope_built_at": fields.Datetime.now(),
            }
        )

    def _download_envelope(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": (
                f"/web/content/{self._name}/{self.id}/envelope_xml/"
                f"{self.envelope_filename}?download=true"
            ),
            "target": "download",
        }

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

    def _record_sii_submission(self, submission):
        self.ensure_one()
        if submission.company_id != self.company_id:
            raise ValidationError(_("The SII transmission belongs to another company."))
        if submission.environment != "certification":
            raise ValidationError(
                _("The linked SII transmission is not Certification.")
            )
        if submission.request_digest != self.envelope_digest:
            raise ValidationError(
                _("The SII transmission does not match this execution envelope.")
            )
        return super(DteCertificationRun, self.sudo()).write(
            {"sii_submission_id": submission.id}
        )

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

    def _record_generation_state(self, state):
        if state not in {"generated", "prepared", "partial", "blocked"}:
            raise ValidationError(_("Invalid execution generation state."))
        return super(DteCertificationRun, self.sudo()).write({"state": state})

    _sql_constraints = [
        (
            "set_revision_uniq",
            "unique(set_id, revision)",
            "Execution revisions must be unique within a certification set.",
        ),
    ]

    @api.depends("line_ids")
    def _compute_line_count(self):
        for run in self:
            run.line_count = len(run.line_ids)

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super
        raise UserError(_("Certification executions are created from a reviewed set."))

    def write(self, vals):  # pylint: disable=method-required-super
        raise UserError(_("Certification execution snapshots are immutable."))

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("Certification execution snapshots cannot be deleted."))

    @api.model
    def _create_from_selection(self, certification_set, selected_cases):
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise UserError(_("Only DTE managers can create certification executions."))
        certification_set.ensure_one()
        cases = selected_cases.sorted("sequence")
        if not cases:
            raise ValidationError(_("Select at least one interpreted SET case."))
        if any(case.set_id != certification_set for case in cases):
            raise ValidationError(_("Every selected case must belong to this SET."))
        if any(case.source_digest != certification_set.source_digest for case in cases):
            raise ValidationError(_("A case snapshot does not match the original SET."))
        if any(case.interpretation_state != "interpreted" for case in cases):
            raise ValidationError(
                _("Only cases without interpretation diagnostics can be snapshotted.")
            )
        selected_ids = set(cases.ids)
        for case in cases:
            target = case.referenced_case_id
            if target and target.id not in selected_ids:
                raise ValidationError(
                    _(
                        "The selection is incomplete: include referenced case "
                        "%(case)s and its ancestors.",
                        case=target.case_identifier,
                    )
                )
            if target and (
                target.set_id != certification_set
                or target.attention_number != case.attention_number
                or target.sequence >= case.sequence
            ):
                raise ValidationError(
                    _("A note reference is outside its valid SET sequence.")
                )

        for case in cases:
            if case.document_code in {"56", "61"} and not case.referenced_case_id:
                raise ValidationError(
                    _(
                        "Case %(case)s has no resolved reference and cannot be "
                        "included in an execution.",
                        case=case.case_identifier,
                    )
                )

        self.env.cr.execute(
            "SELECT id FROM l10n_cl_dte_certification_set WHERE id = %s FOR UPDATE",
            [certification_set.id],
        )
        self.env.cr.execute(
            """
                SELECT COALESCE(MAX(revision), 0)
                  FROM l10n_cl_dte_certification_run
                 WHERE set_id = %s
            """,
            [certification_set.id],
        )
        revision = self.env.cr.fetchone()[0] + 1

        snapshot_rows = []
        line_values = []
        for case in cases:
            interpretation = json.loads(
                json.dumps(case.interpretation or {}, ensure_ascii=False)
            )
            detail_lines = [
                {
                    "sequence": line.sequence,
                    "source_line": line.source_line,
                    "name": line.item_name,
                    "quantity": line.quantity,
                    "unit_price": line.unit_price,
                    "discount_percent": line.discount_percent,
                    "unit_of_measure": line.unit_of_measure,
                    "source_digest": line.source_digest,
                    "interpretation_version": line.interpretation_version,
                }
                for line in case.line_ids.sorted("sequence")
            ]
            snapshot = {
                "set_digest": certification_set.source_digest,
                "case_identifier": case.case_identifier,
                "attention_number": case.attention_number,
                "sequence": case.sequence,
                "section_sequence": case.section_sequence,
                "document_code": case.document_code,
                "source_start_line": case.source_start_line,
                "source_end_line": case.source_end_line,
                "instruction_text": case.instruction_text,
                "interpretation_version": case.interpretation_version,
                "interpretation": interpretation,
                "global_discount_percent": case.global_discount_percent,
                "global_discount_source_line": case.global_discount_source_line,
                "details": detail_lines,
                "reference_case_identifier": case.reference_case_identifier,
                "reference_source_line": case.reference_source_line,
                "reference_reason": case.reference_reason,
                "reference_reason_source_line": case.reference_reason_source_line,
                "reference_target_document_code": (
                    case.referenced_case_id.document_code
                    if case.referenced_case_id
                    else False
                ),
                "reference_code": case.reference_code,
                "reference_code_source_line": case.reference_code_source_line,
                "note_operation": case.note_operation,
                "text_correction": case.text_correction or [],
                "text_correction_content": case.text_correction_content or [],
            }
            snapshot_rows.append(snapshot)
            line_values.append((case, snapshot))

        run_values = {
            "name": _("%(name)s / revision %(revision)s")
            % {"name": certification_set.name, "revision": revision},
            "set_id": certification_set.id,
            "revision": revision,
            "emission_date": fields.Date.context_today(self),
            "created_by": self.env.user.id,
            "set_digest": certification_set.source_digest,
            "snapshot_digest": _canonical_digest(
                {
                    "set_digest": certification_set.source_digest,
                    "cases": snapshot_rows,
                }
            ),
            "state": "draft",
        }
        run = super(DteCertificationRun, self.sudo()).create(run_values)
        created_lines = {}
        for case, snapshot in line_values:
            target_line = created_lines.get(case.referenced_case_id.id)
            run_line = self.env[
                "l10n_cl.dte.certification.run.line"
            ]._create_from_snapshot(
                {
                    "run_id": run.id,
                    "case_id": case.id,
                    "sequence": case.sequence,
                    "case_identifier": case.case_identifier,
                    "document_code": case.document_code,
                    "source_digest": case.source_digest,
                    "snapshot": snapshot,
                    "snapshot_digest": _canonical_digest(snapshot),
                    "referenced_line_id": target_line.id if target_line else False,
                }
            )
            created_lines[case.id] = run_line
        return run

    def action_open(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Certification Execution"),
            "res_model": self._name,
            "view_mode": "form",
            "res_id": self.id,
            "target": "current",
        }


class DteCertificationRunLine(models.Model):
    _name = "l10n_cl.dte.certification.run.line"
    _description = "SII Certification Execution Case Snapshot"
    _order = "sequence, id"
    _rec_name = "case_identifier"
    _check_company_auto = True

    run_id = fields.Many2one(
        "l10n_cl.dte.certification.run",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    company_id = fields.Many2one(
        related="run_id.company_id", store=True, readonly=True, index=True
    )
    case_id = fields.Many2one(
        "l10n_cl.dte.certification.case",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    sequence = fields.Integer(required=True, readonly=True)
    case_identifier = fields.Char(required=True, readonly=True, index=True)
    document_code = fields.Char(required=True, readonly=True, index=True)
    source_digest = fields.Char(required=True, readonly=True, index=True)
    snapshot = fields.Json(required=True, readonly=True)
    snapshot_digest = fields.Char(required=True, readonly=True, index=True)
    referenced_line_id = fields.Many2one(
        "l10n_cl.dte.certification.run.line",
        readonly=True,
        ondelete="restrict",
        check_company=True,
    )
    move_id = fields.Many2one(
        "account.move", readonly=True, ondelete="restrict", check_company=True
    )
    move_digest = fields.Char(readonly=True, index=True)
    generation_state = fields.Selection(
        [
            ("pending", "Pending"),
            ("generated", "Draft Generated"),
            ("unsupported", "Unsupported"),
            ("failed", "Failed"),
        ],
        required=True,
        readonly=True,
        default="pending",
        index=True,
    )
    generation_message = fields.Char(readonly=True)
    selected = fields.Boolean(readonly=True, default=True)
    document_id = fields.Many2one(
        "l10n_cl.electronic.document",
        readonly=True,
        ondelete="restrict",
        check_company=True,
    )
    preparation_state = fields.Selection(
        [
            ("pending", "Pending"),
            ("prepared", "DTE Prepared"),
            ("failed", "Failed"),
        ],
        required=True,
        readonly=True,
        default="pending",
    )
    preparation_message = fields.Char(readonly=True)
    partner_id = fields.Many2one(
        "res.partner", readonly=True, check_company=True, ondelete="restrict"
    )

    _sql_constraints = [
        (
            "run_case_uniq",
            "unique(run_id, case_id)",
            "A case can appear only once in an execution.",
        ),
        (
            "run_sequence_uniq",
            "unique(run_id, sequence)",
            "Case positions must be unique within an execution.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super
        raise UserError(_("Execution case snapshots are created by the SET service."))

    def write(self, vals):  # pylint: disable=method-required-super
        raise UserError(_("Execution case snapshots are immutable."))

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("Execution case snapshots cannot be deleted."))

    @api.model
    def _create_from_snapshot(self, values):
        return super(DteCertificationRunLine, self.sudo()).create(values)

    def _record_generation_result(self, values):
        allowed = {
            "move_id",
            "generation_state",
            "generation_message",
            "partner_id",
        }
        if set(values) - allowed:
            raise ValidationError(_("Invalid generation result fields."))
        if values.get("generation_state") == "generated":
            move = self.env["account.move"].browse(values.get("move_id")).exists()
            if len(move) != 1:
                raise ValidationError(
                    _("A generated case must be linked to one accounting draft.")
                )
            values["move_digest"] = self._digest_move(move)
        return super(DteCertificationRunLine, self.sudo()).write(values)

    @staticmethod
    def _digest_move(move):
        values = {
            "company_id": move.company_id.id,
            "move_type": move.move_type,
            "journal_id": move.journal_id.id,
            "partner_id": move.partner_id.id,
            "invoice_date": fields.Date.to_date(move.invoice_date).isoformat(),
            "document_code": str(move.l10n_latam_document_type_id.code),
            "currency_id": move.currency_id.id,
            "lines": [
                {
                    "name": line.name,
                    "quantity": line.quantity,
                    "price_unit": line.price_unit,
                    "discount": line.discount,
                    "tax_ids": sorted(line.tax_ids.ids),
                    "account_id": line.account_id.id,
                    "uom_id": line.product_uom_id.id,
                }
                for line in move.invoice_line_ids.filtered(
                    lambda line: line.display_type == "product"
                ).sorted("sequence")
            ],
        }
        return _canonical_digest(values)

    def _check_preparation_source(self):
        self.ensure_one()
        run = self.run_id
        move = self.move_id
        if (
            self.generation_state != "generated"
            or not move
            or move.dte_certification_run_line_id != self
            or move.company_id != run.company_id
            or move.state != "draft"
            or str(move.l10n_latam_document_type_id.code) != self.document_code
            or self.source_digest != run.set_digest
            or self.snapshot_digest != _canonical_digest(self.snapshot)
            or self.move_digest != self._digest_move(move)
            or run.company_id.dte_sii_environment != "certification"
        ):
            raise ValidationError(
                _("The generated certification document no longer matches its case.")
            )
        return True

    def _link_certification_note_target(self):
        self.ensure_one()
        target = self.referenced_line_id
        move = self.move_id
        if (
            not target
            or target.run_id != self.run_id
            or target.sequence >= self.sequence
            or target.generation_state != "generated"
            or target.preparation_state != "prepared"
            or not target.document_id
            or target.document_id.preparation_state != "signed"
            or target.document_id.source_move_id != target.move_id
        ):
            raise ValidationError(
                _("Prepare the referenced case before this certification note.")
            )
        snapshot = self.snapshot
        if (
            snapshot.get("reference_target_document_code") != target.document_code
            or not snapshot.get("reference_code")
            or not snapshot.get("reference_reason")
        ):
            raise ValidationError(_("The certification note reference is incomplete."))
        move.write(
            {
                "dte_note_target_document_id": target.document_id.id,
                "dte_note_operation": snapshot.get("note_operation"),
                "dte_note_reason": snapshot.get("reference_reason"),
            }
        )
        return True

    def _record_preparation_result(self, document, state, message=False):
        self.ensure_one()
        if state not in {"prepared", "failed"}:
            raise ValidationError(_("Invalid certification preparation result."))
        if state == "prepared" and (
            not document
            or document.company_id != self.company_id
            or document.source_move_id != self.move_id
            or document.document_code != self.document_code
            or document.preparation_state != "signed"
        ):
            raise ValidationError(_("The prepared DTE does not match this case."))
        return super(DteCertificationRunLine, self.sudo()).write(
            {
                "document_id": document.id if document else False,
                "preparation_state": state,
                "preparation_message": (message or "")[:2000] or False,
            }
        )

    def _validate_envelope_case_result(self):
        """Ensure the signed electronic document still belongs to this frozen case."""
        self.ensure_one()
        document = self.document_id
        if (
            self.generation_state != "generated"
            or self.preparation_state != "prepared"
            or not document
            or document.preparation_state != "signed"
            or document.sii_environment != "certification"
            or document.company_id != self.company_id
            or document.document_code != self.document_code
            or document.source_move_id != self.move_id
            or self.move_id.dte_certification_run_line_id != self
            or self.move_id.company_id != self.company_id
            or self.move_id.state != "draft"
            or self.move_digest != self._digest_move(self.move_id)
            or self.snapshot_digest != _canonical_digest(self.snapshot)
        ):
            raise ValidationError(
                _("The signed DTE no longer matches its frozen certification case.")
            )
        artifact = document._get_signed_artifact()
        artifact.action_verify_integrity()
        try:
            signed_xml = base64.b64decode(artifact.content, validate=True)
        except (binascii.Error, TypeError, ValueError) as error:
            raise ValidationError(
                _("The signed DTE evidence is not valid base64 content.")
            ) from error
        try:
            root = safe_fromstring(signed_xml)
        except (ValueError, etree.XMLSyntaxError) as error:
            raise ValidationError(
                _("The signed DTE XML is not well formed.")
            ) from error
        namespace = "{http://www.sii.cl/SiiDte}"
        encabezado = root.find(f"{namespace}Documento/{namespace}Encabezado")
        id_doc = (
            encabezado.find(f"{namespace}IdDoc") if encabezado is not None else None
        )
        emisor = (
            encabezado.find(f"{namespace}Emisor") if encabezado is not None else None
        )
        receptor = (
            encabezado.find(f"{namespace}Receptor") if encabezado is not None else None
        )
        if (
            root.tag != f"{namespace}DTE"
            or id_doc is None
            or emisor is None
            or receptor is None
            or id_doc.findtext(f"{namespace}TipoDTE") != self.document_code
            or id_doc.findtext(f"{namespace}Folio") != str(document.folio)
            or normalize_rut(emisor.findtext(f"{namespace}RUTEmisor"))
            != normalize_rut(self.company_id.partner_id.vat)
            or normalize_rut(receptor.findtext(f"{namespace}RUTRecep"))
            != normalize_rut(self.move_id.partner_id.vat)
        ):
            raise ValidationError(
                _("The signed DTE fiscal identity differs from its frozen case.")
            )
        return True
