# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import binascii
import hashlib
from collections import Counter

from psycopg2 import IntegrityError

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..services.access import check_certification_access
from ..services.case_instruction_interpreter import INTERPRETER_VERSION
from ..services.set_parser import CertificationSetParser


class DteCertificationSet(models.Model):
    _name = "l10n_cl.dte.certification.set"
    _description = "SII Certification Set Source"
    _order = "create_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, readonly=True)
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, index=True
    )
    source_file = fields.Binary(
        required=True,
        attachment=True,
        readonly=True,
        copy=False,
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    source_filename = fields.Char(required=True, readonly=True, copy=False)
    source_digest = fields.Char(required=True, readonly=True, index=True, copy=False)
    source_byte_count = fields.Integer(required=True, readonly=True)
    imported_by = fields.Many2one(
        "res.users", required=True, readonly=True, default=lambda self: self.env.user
    )
    parse_state = fields.Selection(
        [("pending", "Pending"), ("parsed", "Parsed"), ("blocked", "Blocked")],
        required=True,
        default="pending",
        readonly=True,
    )
    parser_version = fields.Char(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    parsed_structure = fields.Json(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    parse_message = fields.Text(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    case_ids = fields.One2many("l10n_cl.dte.certification.case", "set_id", copy=False)
    execution_ids = fields.One2many(
        "l10n_cl.dte.certification.run", "set_id", copy=False
    )
    case_count = fields.Integer(compute="_compute_case_count")
    case_interpreted_count = fields.Integer(compute="_compute_case_coverage")
    case_review_required_count = fields.Integer(compute="_compute_case_coverage")
    case_unsupported_count = fields.Integer(compute="_compute_case_coverage")
    case_pending_count = fields.Integer(compute="_compute_case_coverage")
    case_outdated_count = fields.Integer(compute="_compute_case_coverage")
    case_coverage_state = fields.Selection(
        [
            ("pending", "Pending"),
            ("complete", "Complete"),
            ("review_required", "Review Required"),
            ("unsupported", "Unsupported Cases"),
        ],
        compute="_compute_case_coverage",
    )

    _sql_constraints = [
        (
            "certification_set_source_uniq",
            "unique(company_id, source_digest)",
            "This source file is already registered for the company.",
        ),
    ]

    @api.depends("case_ids")
    def _compute_case_count(self):
        for record in self:
            record.case_count = len(record.case_ids)

    @api.depends(
        "case_ids.interpretation_state",
        "case_ids.interpretation_version",
        "case_ids.document_code",
    )
    def _compute_case_coverage(self):
        for record in self:
            cases = record.case_ids
            record.case_interpreted_count = len(
                cases.filtered(lambda case: case.interpretation_state == "interpreted")
            )
            record.case_review_required_count = len(
                cases.filtered(
                    lambda case: case.interpretation_state == "review_required"
                )
            )
            record.case_unsupported_count = len(
                cases.filtered(lambda case: case.document_code == "unsupported")
            )
            record.case_pending_count = len(
                cases.filtered(
                    lambda case: case.interpretation_version != INTERPRETER_VERSION
                )
            )
            record.case_outdated_count = len(
                cases.filtered(
                    lambda case: case.interpretation_version
                    and case.interpretation_version != INTERPRETER_VERSION
                )
            )
            if not cases or record.case_pending_count:
                record.case_coverage_state = "pending"
            elif record.case_unsupported_count:
                record.case_coverage_state = "unsupported"
            elif record.case_review_required_count:
                record.case_coverage_state = "review_required"
            else:
                record.case_coverage_state = "complete"

    @api.model
    def _create_from_import(self, company, filename, encoded_source):
        try:
            source = base64.b64decode(encoded_source, validate=True)
        except (binascii.Error, TypeError, ValueError) as error:
            raise ValidationError(_("Upload a valid TXT file.")) from error
        if not filename or not filename.lower().endswith(".txt"):
            raise ValidationError(_("The original SII set must be a TXT file."))
        if not source or len(source) > 2 * 1024 * 1024:
            raise ValidationError(
                _("The TXT file must contain data and cannot exceed 2 MiB.")
            )
        if not company or company.id not in self.env.companies.ids:
            raise ValidationError(_("Select a company available to your user."))
        digest = hashlib.sha256(source).hexdigest()
        domain = [("company_id", "=", company.id), ("source_digest", "=", digest)]
        if self.sudo().search_count(domain):
            raise ValidationError(
                _("This source file is already registered for the company.")
            )
        values = {
            "name": filename.rsplit(".", 1)[0],
            "company_id": company.id,
            "source_file": encoded_source,
            "source_filename": filename,
            "source_digest": digest,
            "source_byte_count": len(source),
            "imported_by": self.env.user.id,
        }
        try:
            with self.env.cr.savepoint():
                return super(DteCertificationSet, self.sudo()).create(values)
        except IntegrityError as error:
            raise ValidationError(
                _("This source file is already registered for the company.")
            ) from error

    def action_parse_source(self):
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise ValidationError(_("Only DTE managers can parse a certification set."))
        from ..services.set_structure_parser import (
            PARSER_VERSION,
            CertificationSetStructureParser,
        )

        for record in self:
            source = base64.b64decode(record.source_file)
            try:
                structure = CertificationSetStructureParser(record.env).parse(source)
            except ValidationError as error:
                record.sudo().write(
                    {
                        "parse_state": "blocked",
                        "parser_version": PARSER_VERSION,
                        "parsed_structure": False,
                        "parse_message": str(error),
                    }
                )
            else:
                has_blockers = bool(structure["diagnostics"])
                record.sudo().write(
                    {
                        "parse_state": "blocked" if has_blockers else "parsed",
                        "parser_version": PARSER_VERSION,
                        "parsed_structure": structure,
                        "parse_message": "\n".join(
                            f"{item['code']}: {item['case']} "
                            f"({item['source_text']})"
                            for item in structure["diagnostics"]
                        )
                        or False,
                    }
                )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("SET analysis complete"),
                "message": _(
                    "Review the detected sections, cases, and any format block."
                ),
                "type": "success"
                if all(record.parse_state == "parsed" for record in self)
                else "warning",
                "sticky": False,
            },
        }

    def action_materialize_cases(self):
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise UserError(_("Only DTE managers can create case snapshots."))
        Case = self.env["l10n_cl.dte.certification.case"]
        for record in self:
            structure = record.parsed_structure
            if not structure or structure.get("source_digest") != record.source_digest:
                raise UserError(
                    _("Analyze the original SET before creating case snapshots.")
                )
            if structure.get("parser_version") != record.parser_version:
                raise UserError(
                    _("The parsed SET version does not match its snapshot.")
                )
            if record.case_ids:
                raise UserError(
                    _("Case snapshots already exist and cannot be replaced.")
                )
            values = []
            sequence = 0
            for section in structure.get("sections", []):
                for case in section.get("cases", []):
                    sequence += 1
                    values.append(
                        {
                            "set_id": record.id,
                            "company_id": record.company_id.id,
                            "attention_number": section["attention_number"],
                            "sequence": sequence,
                            "section_sequence": case["sequence"],
                            "case_identifier": case["identifier"],
                            "document_code": case["document_code"],
                            "source_start_line": case["source_start_line"],
                            "source_end_line": case["source_end_line"],
                            "instruction_text": case["instructions"],
                            "source_digest": record.source_digest,
                            "interpretation_state": (
                                "unsupported"
                                if case["document_code"] == "unsupported"
                                else "identified"
                            ),
                            "diagnostics": [
                                item
                                for item in section.get("diagnostics", [])
                                if item.get("case") == case["identifier"]
                            ],
                        }
                    )
            if not values:
                raise UserError(_("The parsed SET contains no case snapshots."))
            Case._create_from_structure(values)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Case snapshots created"),
                "message": _(
                    "The original case order and source lines were preserved."
                ),
                "type": "success",
                "sticky": False,
            },
        }

    def action_interpret_cases(self):
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise UserError(_("Only DTE managers can interpret certification cases."))
        for record in self:
            if not record.case_ids:
                raise UserError(_("Create case snapshots before interpreting them."))
            expected_digest = record.source_digest
            if any(case.source_digest != expected_digest for case in record.case_ids):
                raise UserError(_("A case snapshot does not match its original SET."))
            if any(
                case.interpretation_version
                and case.interpretation_version != INTERPRETER_VERSION
                for case in record.case_ids
            ):
                raise UserError(
                    _(
                        "This SET contains older interpretations. Use the explicit "
                        "reinterpretation action to update them."
                    )
                )
            record.case_ids._interpret_from_instructions()
            record._resolve_case_references()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Case interpretation complete"),
                "message": _(
                    "Explicit case fields and source-line diagnostics "
                    "are ready for review."
                ),
                "type": "success",
                "sticky": False,
            },
        }

    def action_reinterpret_cases(self):
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise UserError(_("Only DTE managers can reinterpret certification cases."))
        for record in self:
            stale_cases = record.case_ids.filtered(
                lambda case: case.interpretation_version
                and case.interpretation_version != INTERPRETER_VERSION
            )
            if not stale_cases:
                raise UserError(
                    _("This SET has no older case interpretations to update.")
                )
            if any(case.source_digest != record.source_digest for case in stale_cases):
                raise UserError(_("A case snapshot does not match its original SET."))
            stale_cases._interpret_from_instructions(audit_reinterpretation=True)
            record._resolve_case_references()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Case reinterpretation complete"),
                "message": _(
                    "The previous and current interpretations were recorded "
                    "in the case history."
                ),
                "type": "success",
                "sticky": False,
            },
        }

    def _resolve_case_references(self):
        """Close interpreted references within their own attention section."""
        self.ensure_one()
        cases = self.case_ids.sorted("sequence")
        by_section_and_identifier = {
            (case.attention_number, case.case_identifier): case for case in cases
        }
        reference_diagnostics = {
            "reference_case_not_found",
            "reference_case_not_preceding",
            "reference_case_document_type_mismatch",
        }
        for case in cases:
            values = case.interpretation or {}
            diagnostics = [
                item
                for item in (case.diagnostics or [])
                if item.get("code") not in reference_diagnostics
            ]
            target = False
            identifier = case.reference_case_identifier
            if identifier:
                candidate = by_section_and_identifier.get(
                    (case.attention_number, identifier)
                )
                if not candidate:
                    diagnostics.append(
                        {
                            "code": "reference_case_not_found",
                            "source_line": case.reference_source_line,
                            "case_identifier": identifier,
                        }
                    )
                elif candidate.sequence >= case.sequence:
                    diagnostics.append(
                        {
                            "code": "reference_case_not_preceding",
                            "source_line": case.reference_source_line,
                            "case_identifier": identifier,
                        }
                    )
                elif (
                    values.get("reference_document_code")
                    and candidate.document_code != values["reference_document_code"]
                ):
                    diagnostics.append(
                        {
                            "code": "reference_case_document_type_mismatch",
                            "source_line": case.reference_reason_source_line,
                            "case_identifier": identifier,
                            "expected_document_code": values["reference_document_code"],
                            "actual_document_code": candidate.document_code,
                        }
                    )
                else:
                    target = candidate
            case._set_resolved_reference(target, diagnostics)
        return True

    def action_check_case_coverage(self):
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise UserError(
                _("Only DTE managers can check certification case coverage.")
            )
        self.ensure_one()
        if not self.case_ids:
            raise UserError(_("Create case snapshots before checking coverage."))
        if any(case.source_digest != self.source_digest for case in self.case_ids):
            raise UserError(_("A case snapshot does not match its original SET."))
        message = _(
            "Cases: %(total)s; interpreted: %(interpreted)s; "
            "review required: %(review)s; unsupported type: %(unsupported)s; "
            "pending interpretation: %(pending)s."
        ) % {
            "total": self.case_count,
            "interpreted": self.case_interpreted_count,
            "review": self.case_review_required_count,
            "unsupported": self.case_unsupported_count,
            "pending": self.case_pending_count,
        }
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Case coverage"),
                "message": message,
                "type": "success"
                if self.case_coverage_state == "complete"
                else "warning",
                "sticky": True,
            },
        }

    def action_open_execution_wizard(self):
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise UserError(_("Only DTE managers can create certification executions."))
        if self.case_coverage_state != "complete":
            raise UserError(
                _("Interpret and review all SET cases before creating an execution.")
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Select Cases for Execution"),
            "res_model": "l10n_cl.dte.certification.run.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_set_id": self.id,
                "active_model": self._name,
                "active_id": self.id,
            },
        }

    state = fields.Selection(
        [
            ("mapping", "Source Review Required"),
            ("ready", "Source Validated"),
            ("archived", "Archived"),
        ],
        required=True,
        default="mapping",
        readonly=True,
        index=True,
    )

    completion_mode = fields.Selection(
        [
            ("official_sii", "Official SII Certification"),
            ("local_synthetic", "Local Validation with Synthetic Data"),
        ],
        default="official_sii",
        required=True,
        readonly=True,
        copy=False,
    )

    caf_line_ids = fields.One2many(
        "l10n_cl.dte.certification.set.caf", "set_id", copy=False
    )

    run_ids = fields.One2many("l10n_cl.dte.certification.run", "set_id", copy=False)

    can_edit_envelope = fields.Boolean(compute="_compute_can_edit_envelope")

    can_add_caf = fields.Boolean(compute="_compute_can_add_caf")

    def _get_sii_advance_result(self):
        """Return the latest complete assigned-set run with every DTE accepted."""
        self.ensure_one()
        for run in self.run_ids.sorted("revision", reverse=True):
            if run.is_partial:
                continue
            selected_lines = run.line_ids.filtered("selected")
            documents = selected_lines.mapped("document_id")
            submission = run.envelope_submission_ids.sorted(
                "create_date", reverse=True
            )[:1]
            counts = submission.result_counts if submission else []
            informed_count = sum(row.get("INFORMADOS", 0) for row in counts)
            accepted_count = sum(row.get("ACEPTADOS", 0) for row in counts)
            rejected_count = sum(row.get("RECHAZADOS", 0) for row in counts)
            observation_count = sum(row.get("REPAROS", 0) for row in counts)
            if (
                submission
                and submission.state == "accepted"
                and submission.track_id
                and submission.submitted_at
                and counts
                and len(selected_lines) == len(self.case_ids)
                and len(documents) == len(selected_lines)
                and informed_count == len(self.case_ids)
                and accepted_count == informed_count
                and not rejected_count
                and not observation_count
            ):
                return run, submission
        return self.env["l10n_cl.dte.certification.run"], self.env[
            "l10n_cl.sii.envelope.submission"
        ]

    def _get_folio_requirements(self):
        """Read remaining full-set demand and free configured CAF capacity."""
        self.ensure_one()
        latest_run = self.run_ids.sorted("revision", reverse=True)[:1]
        numbered_cases = latest_run.line_ids.filtered(
            lambda line: line.document_id.folio
        ).case_id
        required_by_code = Counter(
            (self.case_ids - numbered_cases).mapped("document_code")
        )
        requirements = []
        folio_model = self.env["l10n_cl.caf.folio"]
        for code, required in sorted(required_by_code.items()):
            if code not in {"33", "34", "56", "61"}:
                continue
            ranges = []
            for caf in self._cafs_for_document_code(code):
                if caf.state != "validated":
                    continue
                if ranges and caf.folio_start <= ranges[-1][1] + 1:
                    ranges[-1][1] = max(ranges[-1][1], caf.folio_end)
                else:
                    ranges.append([caf.folio_start, caf.folio_end])
            available = sum(
                end
                - start
                + 1
                - folio_model.search_count(
                    [
                        ("company_id", "=", self.company_id.id),
                        ("document_code", "=", code),
                        ("number", ">=", start),
                        ("number", "<=", end),
                    ]
                )
                for start, end in ranges
            )
            missing = max(0, required - available)
            requirements.append(
                {
                    "document_code": code,
                    "required": required,
                    "available": available,
                    "missing": missing,
                    "message": _(
                        "DTE %(code)s: %(required)s needed, %(available)s "
                        "available; %(missing)s missing.",
                        code=code,
                        required=required,
                        available=available,
                        missing=missing,
                    ),
                }
            )
        return requirements

    def _check_certification_access(self):
        for record in self:
            check_certification_access(self.env, record.company_id)

    def _check_signing_upload_access(self):
        self.ensure_one()
        self._check_certification_access()
        self.check_access("write")
        self.env["certificate.certificate"].check_access("create")
        self._lock_configuration()
        if not self.can_edit_envelope:
            raise UserError(
                _("Configuration is frozen after document generation begins.")
            )

    def _populate_test_receiver_activities(self, target_mode=None):
        from ..services.synthetic_receiver_activity_service import (
            SyntheticReceiverActivityService,
        )

        self.ensure_one()
        self._check_certification_access()
        self._lock_configuration()
        self._verify_source_integrity()
        self.case_ids.invalidate_recordset()
        if self.state not in {"mapping", "ready"} or self.run_ids:
            raise UserError(_("Generate test activities before any execution exists."))
        if self.parser_version != "2":
            raise UserError(
                _("Reinterpret this SET with parser v2 before generating activities.")
            )
        plan = SyntheticReceiverActivityService().build_plan(self)
        if not plan:
            raise UserError(
                _(
                    "No cases can receive generated activities without replacing "
                    "supplied text."
                )
            )
        with self.env.cr.savepoint():
            for item in plan:
                case_id = item["case_id"]
                case = self.case_ids.filtered(
                    lambda case, case_id=case_id: case.id == case_id
                )
                case._store_synthetic_receiver_activity(item)
            self._mark_completion_review(
                {
                    "mode": target_mode or self.completion_mode,
                    "contains_synthetic": True,
                    "digest": CertificationSetParser().canonical_digest(plan),
                }
            )
            self._refresh_case_diagnostics(source_only=None)
        return True

    def _check_can_add_caf(self):
        self.ensure_one()
        self._check_certification_access()
        self._lock_configuration()
        self._check_no_active_caf_requests()
        self.invalidate_recordset(["can_new_run", "can_edit_envelope", "can_add_caf"])
        if not self.can_add_caf:
            raise UserError(
                _(
                    "Import additional CAFs before generating the new draft execution. "
                    "Previous executions must have a verified final SII result."
                )
            )

    def _mark_completion_review(self, values):
        self.ensure_one()
        self._check_certification_access()
        if self.state not in {"mapping", "ready"} or self.run_ids:
            raise UserError(
                _("Completion review is frozen after document generation begins.")
            )
        if values.get("mode") not in {"official_sii", "local_synthetic"}:
            raise UserError(_("Invalid completion review mode."))
        return self._write_internal(
            {
                "completion_mode": values["mode"],
                "contains_synthetic_completion": bool(values.get("contains_synthetic")),
                "completion_digest": values["digest"],
                "completion_reviewed_at": fields.Datetime.now(),
                "completion_reviewed_by": self.env.user.id,
            }
        )

    def _verify_source_integrity(self):
        parser = CertificationSetParser()
        # Form reads request binary sizes; integrity always needs the raw bytes.
        for record in self.with_context(bin_size=False, bin_size_source_file=False):
            record.invalidate_recordset(
                [
                    "source_file",
                    "source_digest",
                    "source_byte_count",
                    "normalized_payload",
                    "normalized_digest",
                ]
            )
            try:
                source = base64.b64decode(record.source_file, validate=True)
            except (binascii.Error, TypeError, ValueError) as error:
                raise UserError(
                    _("The source fingerprint does not match its bytes.")
                ) from error
            if (
                len(source) != record.source_byte_count
                or hashlib.sha256(source).hexdigest() != record.source_digest
            ):
                raise UserError(_("The source fingerprint does not match its bytes."))
            payload = record.normalized_payload
            missing_legacy_digest = (
                record.state != "mapping" and not record.normalized_digest
            )
            if (
                not isinstance(payload, dict)
                or payload.get("source_digest") != record.source_digest
                or payload.get("attention_number") != record.attention_number
                or (
                    record.normalized_digest
                    and parser.canonical_digest(payload) != record.normalized_digest
                )
            ):
                raise UserError(
                    _("The normalized SII cases no longer match their fingerprint.")
                )
            if missing_legacy_digest:
                # Older imported sets may predate the stored normalized digest.
                # Rebuild the interpretation from its immutable TXT before
                # backfilling the digest; never bless a payload that cannot be
                # reproduced byte-for-byte by the current parser.
                if record._parse_normalized_payload(source, parser) != payload:
                    raise UserError(
                        _("The normalized SII cases no longer match their fingerprint.")
                    )
                record._write_internal(
                    {"normalized_digest": parser.canonical_digest(payload)}
                )

    def _cafs_for_document_code(self, document_code):
        """Return every configured CAF range for a supported DTE type."""
        self.ensure_one()
        today = fields.Date.context_today(self)
        return (
            self.caf_line_ids.filtered(
                lambda line: (
                    line.document_code == str(document_code)
                    and line.caf_id.sii_environment == "certification"
                    and not line.caf_id.is_synthetic
                    and line.caf_id.state in {"validated", "exhausted"}
                    and (not line.caf_id.date_to or line.caf_id.date_to >= today)
                )
            )
            .mapped("caf_id")
            .sorted(key=lambda caf: (caf.folio_start, caf.folio_end, caf.id))
        )

    def _caf_for_document_code(self, document_code, folio=None):
        """Return the configured CAF matching a type and, when given, folio."""
        cafs = self._cafs_for_document_code(document_code)
        if folio is not None:
            cafs = cafs.filtered(lambda caf: caf.folio_start <= folio <= caf.folio_end)
        return cafs[:1]

    def _caf_with_available_folio(self, document_code):
        """Choose the first configured range that still has an unreserved folio."""
        self.ensure_one()
        for caf in self._cafs_for_document_code(document_code):
            caf._lock_range()
            if caf.state != "validated" or (
                caf.date_to and caf.date_to < fields.Date.context_today(caf)
            ):
                continue
            self.env.cr.execute(
                """SELECT COUNT(*) FROM l10n_cl_caf_folio
                   WHERE authorization_id = %s""",
                [caf.id],
            )
            occupied = self.env.cr.fetchone()[0]
            if occupied < caf.folio_end - caf.folio_start + 1:
                return caf
        return self.env["l10n_cl.caf.authorization"]

    def _check_no_active_caf_requests(self):
        for record in self:
            if record.caf_request_ids.filtered(
                lambda request: request.state in {"queued", "running", "uncertain"}
            ):
                raise UserError(
                    _(
                        "Resolve queued, running or uncertain CAF requests before "
                        "generating documents."
                    )
                )

    def _lock_configuration(self):
        for record in self.sorted("id"):
            self.env.cr.execute(
                "SELECT id FROM l10n_cl_dte_certification_set WHERE id = %s FOR UPDATE",
                [record.id],
            )
            self.env.cr.execute(
                "SELECT id FROM l10n_cl_dte_certification_run "
                "WHERE set_id = %s ORDER BY id FOR UPDATE",
                [record.id],
            )
        self.invalidate_recordset(["state", "run_ids"])
        self.mapped("run_ids").invalidate_recordset(["state", "input_digest"])

    def _refresh_case_diagnostics(self, source_only=False):
        for record in self:
            diagnostics = []
            for case in record.case_ids.sorted("sequence"):
                case_source_only = source_only
                if case_source_only is None:
                    case_source_only = not (
                        case.partner_id or any(case.line_ids.mapped("product_id"))
                    )
                problems = case._check_case_readiness(source_only=case_source_only)
                if problems:
                    diagnostics.append(
                        {"case": case.case_identifier, "problems": problems}
                    )
            record._write_internal({"diagnostics": diagnostics})

    def _write_internal(self, vals):
        return super().write(vals)

    @api.depends("state", "run_ids.state", "run_ids.input_digest")
    def _compute_can_edit_envelope(self):
        for record in self:
            record.can_edit_envelope = bool(
                record.state in ("mapping", "ready")
                and not record.run_ids.filtered(
                    lambda run: run.state != "draft" or run.input_digest
                )
            )

    @api.depends(
        "can_operate",
        "can_edit_envelope",
        "can_new_run",
        "run_ids.revision",
        "run_ids.state",
        "run_ids.input_digest",
        "run_ids.line_ids.move_id",
        "run_ids.line_ids.document_id",
        "caf_request_ids.state",
    )
    def _compute_can_add_caf(self):
        for record in self:
            latest = record.run_ids.sorted("revision", reverse=True)[:1]
            clean_revision = bool(
                latest
                and latest.state == "draft"
                and not latest.input_digest
                and not latest.line_ids.move_id
                and not latest.line_ids.document_id
            )
            record.can_add_caf = bool(
                record.can_operate
                and not record.caf_request_ids.filtered(
                    lambda request: request.state in {"queued", "running", "uncertain"}
                )
                and (
                    record.can_edit_envelope or (clean_revision and record.can_new_run)
                )
            )

    normalized_payload = fields.Json(readonly=True, copy=False)

    normalized_digest = fields.Char(readonly=True, copy=False)

    attention_number = fields.Char(index=True)

    caf_request_ids = fields.One2many(
        "l10n_cl.dte.certification.caf.request", "set_id", copy=False
    )

    can_operate = fields.Boolean(compute="_compute_can_operate")

    can_new_run = fields.Boolean(compute="_compute_can_new_run")

    def _parse_normalized_payload(self, source, parser=None):
        """Rebuild the stored interpretation from its original TXT bytes."""
        self.ensure_one()
        parser = parser or CertificationSetParser()
        payload = self.normalized_payload
        if self.parser_profile in {
            "sii_txt_v1",
            "sii_section_v1",
            "sii_txt_v2",
            "sii_section_v2",
        }:
            return parser.parse(
                source,
                self.source_encoding,
                self.parser_profile,
                selected_attention=self.attention_number,
            )
        if self.parser_profile in {"reviewed_mapping_v1", "reviewed_mapping_v2"}:
            return parser.validate_mapping(
                source,
                {
                    key: payload[key]
                    for key in (
                        "schema_version",
                        "source_digest",
                        "attention_number",
                        "cases",
                    )
                },
                self.source_encoding,
                version=2 if self.parser_profile.endswith("v2") else 1,
            )
        return parser.validate_section_mapping(
            source,
            {
                key: payload[key]
                for key in (
                    "schema_version",
                    "source_digest",
                    "attention_number",
                    "section_start_line",
                    "section_end_line",
                    "acknowledged_section_lines",
                    "cases",
                )
            },
            self.source_encoding,
            self.attention_number,
            version=2 if self.parser_profile.endswith("v2") else 1,
        )

    source_encoding = fields.Selection(
        [("utf-8", "UTF-8"), ("iso-8859-1", "ISO-8859-1")],
        required=True,
        default="utf-8",
    )

    parser_profile = fields.Selection(
        [
            ("sii_txt_v1", "SII text v1"),
            ("reviewed_mapping_v1", "Reviewed mapping v1"),
            ("sii_section_v1", "SII section v1"),
            ("reviewed_section_v1", "Reviewed section v1"),
            ("sii_txt_v2", "SII text v2"),
            ("reviewed_mapping_v2", "Reviewed mapping v2"),
            ("sii_section_v2", "SII section v2"),
            ("reviewed_section_v2", "Reviewed section v2"),
        ],
        required=True,
        default="sii_txt_v1",
    )
