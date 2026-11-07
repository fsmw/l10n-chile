# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hashlib
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


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
            lambda line: line.generation_state == "generated"
            and line.preparation_state == "prepared"
        )
        failed = self.line_ids.filtered(
            lambda line: line.generation_state == "generated"
            and line.preparation_state == "failed"
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
                "preparation_message": (message or False)[:2000],
            }
        )
