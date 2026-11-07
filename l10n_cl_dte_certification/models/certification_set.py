# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import binascii
import hashlib

from psycopg2 import IntegrityError

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..services.case_instruction_interpreter import INTERPRETER_VERSION


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
