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
        if any(line.generation_state != "pending" for line in invoice_lines):
            raise UserError(_("Only pending certification cases can be generated."))
        if not invoice_lines:
            raise ValidationError(
                _("Select at least one pending document of type 33 or 34.")
            )
        self._check_journal(journal, run.company_id)
        self._check_income_account(income_account, run.company_id)
        if any(line.document_code == "33" for line in invoice_lines):
            self._check_vat(vat_tax, run.company_id)

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

        for line in lines.filtered(lambda item: item.document_code not in {"33", "34"}):
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

    def _document_type(self, journal, code):
        country = journal.company_id.country_id
        if not country or country.code != "CL":
            raise ValidationError(
                _("The sales journal must belong to a Chilean company.")
            )
        types = self.env["l10n_latam.document.type"].search(
            [
                ("country_id", "=", country.id),
                ("code", "=", code),
                ("internal_type", "=", "invoice"),
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
