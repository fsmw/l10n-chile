# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hashlib
import json
from decimal import Decimal, InvalidOperation

from odoo import Command, _
from odoo.exceptions import AccessError, UserError, ValidationError


class CaseGenerationService:
    """Create ordinary draft invoices from reviewed, immutable case snapshots."""

    def __init__(self, env):
        self.env = env

    def generate(
        self,
        run,
        journal=None,
        vat_tax=None,
        income_account=None,
        partner_by_line=None,
        selected_lines=None,
    ):
        run.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(_("Only DTE managers can generate certification drafts."))
        if run.company_id != self.env.company:
            raise AccessError(_("Use the execution company as the active company."))
        if run.state not in {"draft", "partial"}:
            raise UserError(_("This execution no longer accepts document generation."))
        lines = (run.line_ids if selected_lines is None else selected_lines).exists()
        if not lines or any(line.run_id != run for line in lines):
            raise ValidationError(_("Select cases from this execution."))
        partners = partner_by_line or {}
        invoice_lines = lines.filtered(lambda line: line.document_code in {"33", "34"})
        correction_lines = lines.filtered(
            lambda line: line.document_code == "61"
            and line.snapshot.get("note_operation") == "text"
        )
        text_cancellation_lines = lines.filtered(
            lambda line: line.document_code == "56"
            and line.snapshot.get("note_operation") == "cancel"
            and line.snapshot.get("reference_target_document_code") == "61"
        )
        supported_lines = invoice_lines | correction_lines | text_cancellation_lines
        if any(line.generation_state != "pending" for line in supported_lines):
            raise UserError(_("Only pending certification cases can be generated."))
        if not supported_lines:
            raise ValidationError(
                _(
                    "Select a pending document of type 33 or 34, a text-correction "
                    "credit note of type 61, or its zero-value type-56 cancellation."
                )
            )
        self._check_journal(journal, run.company_id)
        self._check_income_account(income_account, run.company_id)
        if any(line.document_code == "33" for line in invoice_lines):
            self._check_vat(vat_tax, run.company_id)

        for line in correction_lines:
            self._validate_text_correction_snapshot(line, lines)
        for line in text_cancellation_lines:
            self._validate_text_cancellation_snapshot(line, lines)

        prepared = []
        for line in invoice_lines:
            partner = partners.get(line.id)
            self._check_partner(partner, run.company_id, line)
            doc_type = self._document_type(journal, line.document_code)
            rows = self._prepare_rows(line, vat_tax, income_account)
            prepared.append((line, partner, doc_type, rows))

        created = self.env["l10n_cl.dte.certification.run.line"]
        for line, partner, doc_type, rows in prepared:
            values = {
                "move_type": "out_invoice",
                "company_id": run.company_id.id,
                "journal_id": journal.id,
                "partner_id": partner.id,
                "invoice_date": run.emission_date,
                "l10n_latam_document_type_id": doc_type.id,
                "dte_certification_run_line_id": line.id,
                "invoice_line_ids": [Command.create(row) for row in rows],
            }
            move = self.env["account.move"].create(values)
            line._record_generation_result(
                {
                    "move_id": move.id,
                    "partner_id": partner.id,
                    "generation_state": "generated",
                    "generation_message": False,
                }
            )
            created |= line

        for line in correction_lines.sorted("sequence"):
            target_line = self._check_text_correction_target(line, lines)
            partner = target_line.partner_id
            if not partner:
                raise ValidationError(
                    _(
                        "The referenced invoice for case %(case)s has no customer.",
                        case=line.case_identifier,
                    )
                )
            values = {
                "move_type": "out_refund",
                "company_id": run.company_id.id,
                "journal_id": journal.id,
                "partner_id": partner.id,
                "invoice_date": run.emission_date,
                "l10n_latam_document_type_id": self._document_type(
                    journal, "61", internal_type="credit_note"
                ).id,
                "reversed_entry_id": target_line.move_id.id,
                "dte_certification_run_line_id": line.id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": _("Text correction (no monetary effect)"),
                            "quantity": 1,
                            "price_unit": 0,
                            "discount": 0,
                            "account_id": income_account.id,
                            "tax_ids": [Command.clear()],
                        }
                    )
                ],
            }
            move = self.env["account.move"].create(values)
            line._record_generation_result(
                {
                    "move_id": move.id,
                    "partner_id": partner.id,
                    "generation_state": "generated",
                    "generation_message": False,
                }
            )
            created |= line

        created |= self._generate_text_cancellation_drafts(
            run, text_cancellation_lines, lines, journal, income_account
        )

        for line in lines - supported_lines:
            if line.generation_state == "pending":
                line._record_generation_result(
                    {
                        "generation_state": "unsupported",
                        "generation_message": _(
                            "Document type %(code)s is not included in this "
                            "generation package.",
                            code=line.document_code,
                        ),
                    }
                )
        remaining = run.line_ids.filtered(
            lambda item: item.generation_state in {"pending", "failed", "unsupported"}
        )
        run._record_generation_state("partial" if remaining else "generated")
        return created

    def _generate_text_cancellation_drafts(
        self, run, cancellation_lines, selected_lines, journal, income_account
    ):
        """Create draft-only type-56 cancellations for zero-value type-61 notes."""
        created = self.env["l10n_cl.dte.certification.run.line"]
        for line in cancellation_lines.sorted("sequence"):
            target_line = self._check_text_cancellation_target(line, selected_lines)
            values = {
                "move_type": "out_invoice",
                "company_id": run.company_id.id,
                "journal_id": journal.id,
                "partner_id": target_line.partner_id.id,
                "invoice_date": run.emission_date,
                "l10n_latam_document_type_id": self._document_type(
                    journal, "56", internal_type="debit_note"
                ).id,
                "debit_origin_id": target_line.move_id.id,
                "dte_certification_run_line_id": line.id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": _(
                                "Cancellation of zero-value text correction "
                                "(no monetary effect)"
                            ),
                            "quantity": 1,
                            "price_unit": 0,
                            "discount": 0,
                            "account_id": income_account.id,
                            "tax_ids": [Command.clear()],
                        }
                    )
                ],
            }
            move = self.env["account.move"].create(values)
            line._record_generation_result(
                {
                    "move_id": move.id,
                    "partner_id": target_line.partner_id.id,
                    "generation_state": "generated",
                    "generation_message": False,
                }
            )
            created |= line
        return created

    def _check_journal(self, journal, company):
        if (
            not journal
            or journal.company_id != company
            or not company.country_id
            or company.country_id.code != "CL"
            or company.currency_id.name != "CLP"
            or journal.type != "sale"
            or not journal.active
            or not journal.l10n_latam_use_documents
        ):
            raise ValidationError(
                _(
                    "Choose an active sales journal enabled for Chilean "
                    "electronic documents."
                )
            )

    def _check_vat(self, tax, company):
        if (
            not tax
            or tax.company_id != company
            or tax.type_tax_use != "sale"
            or tax.amount != 19
            or tax.amount_type != "percent"
            or tax.children_tax_ids
        ):
            raise ValidationError(
                _("Choose a single 19% sales VAT tax for type-33 cases.")
            )

    def _check_income_account(self, account, company):
        if (
            not account
            or company not in account.company_ids
            or account.account_type != "income"
            or account.deprecated
        ):
            raise ValidationError(
                _("Choose an active sales income account for generated lines.")
            )

    def _check_partner(self, partner, company, line):
        if not partner:
            raise ValidationError(
                _("Choose a customer for case %(case)s.", case=line.case_identifier)
            )
        if partner.company_id and partner.company_id != company:
            raise ValidationError(_("The customer belongs to another company."))
        if (
            not partner.active
            or not partner.vat
            or not partner.country_id
            or partner.country_id.code != "CL"
        ):
            raise ValidationError(
                _(
                    "Choose an active Chilean customer with a RUT for case %(case)s.",
                    case=line.case_identifier,
                )
            )
        caf = self.env["l10n_cl.caf.authorization"]
        normalized_vat = caf._normalize_vat(partner.vat)
        if not caf._is_valid_vat(normalized_vat):
            raise ValidationError(
                _(
                    "The customer RUT is invalid for case %(case)s.",
                    case=line.case_identifier,
                )
            )

    def _check_text_correction_target(self, line, selected_lines):
        target = line.referenced_line_id
        if (
            target
            and target.generation_state == "pending"
            and target not in selected_lines
        ):
            raise ValidationError(
                _(
                    "Select the referenced invoice along with text-correction "
                    "case %(case)s.",
                    case=line.case_identifier,
                )
            )
        if (
            not target
            or target.run_id != line.run_id
            or target.document_code != "33"
            or target.sequence >= line.sequence
            or target.generation_state != "generated"
            or not target.move_id
            or target.move_id.state != "draft"
            or target.move_id.dte_certification_run_line_id != target
            or target.move_id.move_type != "out_invoice"
        ):
            raise ValidationError(
                _(
                    "Text-correction case %(case)s requires its earlier generated "
                    "type-33 draft in the same execution.",
                    case=line.case_identifier,
                )
            )
        return target

    def _validate_text_correction_snapshot(self, line, selected_lines):
        target = line.referenced_line_id
        if (
            not target
            or target.run_id != line.run_id
            or target.document_code != "33"
            or target.sequence >= line.sequence
        ):
            raise ValidationError(
                _(
                    "Text-correction case %(case)s requires an earlier type-33 "
                    "invoice in the same execution.",
                    case=line.case_identifier,
                )
            )
        if target.generation_state == "pending" and target not in selected_lines:
            raise ValidationError(
                _(
                    "Select the referenced invoice along with text-correction "
                    "case %(case)s.",
                    case=line.case_identifier,
                )
            )
        if target.generation_state not in {"pending", "generated"}:
            raise ValidationError(
                _(
                    "The referenced invoice for text-correction case %(case)s "
                    "is not available for draft generation.",
                    case=line.case_identifier,
                )
            )
        if (
            line.snapshot.get("reference_code") != "2"
            or line.snapshot.get("reference_target_document_code") != "33"
            or not line.snapshot.get("reference_reason")
        ):
            raise ValidationError(
                _(
                    "Text-correction case %(case)s has incomplete reference data.",
                    case=line.case_identifier,
                )
            )
        content = line.snapshot.get("text_correction_content") or []
        if sorted(item.get("kind") for item in content) != [
            "debe_decir",
            "dice",
        ] or any(not item.get("value") for item in content):
            raise ValidationError(
                _(
                    "Text-correction case %(case)s has incomplete correction text.",
                    case=line.case_identifier,
                )
            )
        if line.snapshot_digest != self._snapshot_digest(line.snapshot):
            raise ValidationError(
                _(
                    "Case %(case)s no longer matches its execution snapshot.",
                    case=line.case_identifier,
                )
            )
        if line.source_digest != line.run_id.set_digest:
            raise ValidationError(
                _(
                    "Case %(case)s no longer matches its original SET.",
                    case=line.case_identifier,
                )
            )

    def _validate_text_cancellation_snapshot(self, line, selected_lines):
        target = line.referenced_line_id
        if (
            not target
            or target.run_id != line.run_id
            or target.document_code != "61"
            or target.sequence >= line.sequence
            or target.snapshot.get("note_operation") != "text"
        ):
            raise ValidationError(
                _(
                    "Type-56 case %(case)s must cancel an earlier type-61 "
                    "text-correction case in the same execution.",
                    case=line.case_identifier,
                )
            )
        if target.generation_state == "pending" and target not in selected_lines:
            raise ValidationError(
                _(
                    "Select the referenced type-61 correction along with case "
                    "%(case)s.",
                    case=line.case_identifier,
                )
            )
        if target.generation_state not in {"pending", "generated"}:
            raise ValidationError(
                _(
                    "The referenced type-61 correction for case %(case)s is not "
                    "available for draft generation.",
                    case=line.case_identifier,
                )
            )
        if (
            line.snapshot.get("reference_code") != "1"
            or line.snapshot.get("reference_target_document_code") != "61"
            or not line.snapshot.get("reference_reason")
            or line.snapshot.get("text_correction_content")
        ):
            raise ValidationError(
                _(
                    "Type-56 cancellation case %(case)s has inconsistent reference "
                    "data.",
                    case=line.case_identifier,
                )
            )
        if line.snapshot_digest != self._snapshot_digest(line.snapshot):
            raise ValidationError(
                _(
                    "Case %(case)s no longer matches its execution snapshot.",
                    case=line.case_identifier,
                )
            )
        if line.source_digest != line.run_id.set_digest:
            raise ValidationError(
                _(
                    "Case %(case)s no longer matches its original SET.",
                    case=line.case_identifier,
                )
            )

    def _check_text_cancellation_target(self, line, selected_lines):
        target = line.referenced_line_id
        if (
            target
            and target.generation_state == "pending"
            and target not in selected_lines
        ):
            raise ValidationError(
                _(
                    "Select the referenced type-61 correction along with case "
                    "%(case)s.",
                    case=line.case_identifier,
                )
            )
        if (
            not target
            or target.run_id != line.run_id
            or target.document_code != "61"
            or target.snapshot.get("note_operation") != "text"
            or target.sequence >= line.sequence
            or target.generation_state != "generated"
            or not target.move_id
            or target.move_id.state != "draft"
            or target.move_id.dte_certification_run_line_id != target
            or target.move_id.move_type != "out_refund"
            or target.move_id.reversed_entry_id != target.referenced_line_id.move_id
            or not target.partner_id
            or target.move_id.partner_id != target.partner_id
            or target.move_id.amount_total != 0
        ):
            raise ValidationError(
                _(
                    "Type-56 case %(case)s requires its earlier generated "
                    "zero-value type-61 text correction draft.",
                    case=line.case_identifier,
                )
            )
        return target

    @staticmethod
    def _snapshot_digest(snapshot):
        return hashlib.sha256(
            json.dumps(
                snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()

    def _document_type(self, journal, code, internal_type="invoice"):
        country = journal.company_id.country_id
        if not country or country.code != "CL":
            raise ValidationError(
                _("The sales journal must belong to a Chilean company.")
            )
        types = self.env["l10n_latam.document.type"].search(
            [
                ("country_id", "=", country.id),
                ("code", "=", code),
                ("internal_type", "=", internal_type),
            ]
        )
        if len(types) != 1:
            raise ValidationError(
                _(
                    "Document type %(code)s is unavailable for the selected journal.",
                    code=code,
                )
            )
        return types

    def _prepare_rows(self, line, vat_tax, income_account):
        snapshot = line.snapshot
        digest = hashlib.sha256(
            json.dumps(
                snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        if (
            line.source_digest != line.run_id.set_digest
            or digest != line.snapshot_digest
        ):
            raise ValidationError(
                _(
                    "Case %(case)s no longer matches its source.",
                    case=line.case_identifier,
                )
            )
        if snapshot.get("global_discount_percent"):
            raise ValidationError(
                _(
                    "Global discounts require a later generation package "
                    "(case %(case)s).",
                    case=line.case_identifier,
                )
            )
        details = snapshot.get("details") or []
        if not details:
            raise ValidationError(
                _(
                    "Case %(case)s has no interpreted detail lines.",
                    case=line.case_identifier,
                )
            )
        rows = []
        for detail in details:
            try:
                quantity = self._decimal(detail.get("quantity"), positive=True)
                price = self._decimal(detail.get("unit_price"), allow_zero=True)
                discount = self._decimal(
                    detail.get("discount_percent") or "0", allow_zero=True
                )
            except (InvalidOperation, ValueError) as error:
                raise ValidationError(
                    _(
                        "Invalid quantity, price, or discount in case %(case)s.",
                        case=line.case_identifier,
                    )
                ) from error
            if discount < 0 or discount > 100:
                raise ValidationError(_("Discount must be between 0 and 100 percent."))
            values = {
                "name": detail.get("name"),
                "quantity": float(quantity),
                "price_unit": float(price),
                "discount": float(discount),
                "account_id": income_account.id,
                "tax_ids": [Command.set([vat_tax.id])]
                if line.document_code == "33"
                else [Command.clear()],
            }
            if not values["name"]:
                raise ValidationError(
                    _("Every generated detail must have a description.")
                )
            rows.append(values)
        return rows

    @staticmethod
    def _decimal(value, positive=False, allow_zero=False):
        if value is None:
            raise ValueError("missing value")
        result = Decimal(str(value))
        if (
            not result.is_finite()
            or (positive and result <= 0)
            or (not allow_zero and not positive and result < 0)
        ):
            raise ValueError("invalid value")
        return result
