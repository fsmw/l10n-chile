# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tools import SQL

from .binding_context import internal_context
from .common import (
    BINDING,
    IncomingAccountError,
    canonical_folio,
    reject,
    source_snapshot,
    vat,
)
from .comparison_service import IncomingAccountComparisonService
from .evidence_service import IncomingAccountEvidenceService
from .mapping_service import IncomingAccountMappingService
from .policy_service import IncomingAccountPolicyService


class IncomingAccountBindingService:
    def __init__(self, env):
        self.env = env
        self.policy = IncomingAccountPolicyService(env)
        self.evidence = IncomingAccountEvidenceService(env)
        self.mapping = IncomingAccountMappingService(env)
        self.comparison = IncomingAccountComparisonService(env)

    def prepare(
        self,
        incoming,
        overrides=None,
        journal=None,
        accounting_date=None,
        mode="create",
        target=None,
    ):
        self.policy.check_access(incoming)
        company = self.env.company
        result = dict(
            company_id=company.id,
            incoming_document_id=incoming.id,
            currency_id=company.currency_id.id,
            mode=mode,
            journal_id=(journal or company.dte_inbox_account_journal_id).id,
            accounting_date=accounting_date or fields.Date.context_today(incoming),
            partner_id=False,
            lines=[],
            diagnostics=[],
            candidates=[],
        )
        try:
            document = self.evidence.read(incoming)
            self.policy.check_eligibility(incoming, document)
            result["document"] = document
            result["diagnostics"].extend(document.get("information", []))
            partner = self.policy.resolve_supplier(
                company, document["identity"]["issuer_vat"]
            )
            result["partner_id"] = partner.id
            journal = self.env["account.journal"].browse(result["journal_id"])
            self.policy.resolve_document_type(
                journal, partner, document["identity"]["document_code"]
            )
            if fields.Date.to_date(
                result["accounting_date"]
            ) <= company._get_user_fiscal_lock_date(journal):
                reject(
                    self.env,
                    "unsupported_feature",
                    source_value=result["accounting_date"],
                )
            candidates = self._find_candidates(company, partner, document)
            if any(not m.has_access("read") for m in candidates):
                reject(self.env, "duplicate_bill")
            result["candidates"] = candidates.ids
            if len(candidates) > 1 or candidates.filtered(lambda m: m.state != "draft"):
                reject(self.env, "duplicate_bill")
            for source in document["lines"]:
                selected = (overrides or {}).get(source["ordinal"], {})
                try:
                    mapped = self.mapping.resolve(
                        dict(document, lines=[source]),
                        partner,
                        overrides,
                        validate=False,
                    )[0]
                    result["lines"].append(mapped)
                    self.mapping._validate_line(source, mapped, company)
                except IncomingAccountError as error:
                    result["diagnostics"].append(error.diagnostic)
                    if not any(
                        v["source"]["ordinal"] == source["ordinal"]
                        for v in result["lines"]
                    ):
                        result["lines"].append(
                            dict(
                                source=source,
                                mapping_id=False,
                                product_id=selected.get("product_id", False),
                                account_id=selected.get("account_id", False),
                                tax_id=selected.get("tax_id", False),
                                uom_id=selected.get("uom_id", False),
                                existing_line_id=selected.get(
                                    "existing_line_id", False
                                ),
                            )
                        )
            if not any(d["severity"] == "blocker" for d in result["diagnostics"]):
                self.comparison.preview(document, result["lines"], journal)
                if mode == "link":
                    if (
                        not target
                        or target not in candidates
                        or target.state != "draft"
                    ):
                        reject(self.env, "existing_bill_incompatible")
                    target.check_access("write")
                    self.comparison.compare_move(
                        document,
                        target,
                        {
                            v["source"]["ordinal"]: v["existing_line_id"]
                            for v in result["lines"]
                        },
                    )
                elif candidates:
                    reject(self.env, "duplicate_bill")
                result["preview_digest"] = self._digest(result, target)
        except IncomingAccountError as error:
            result["diagnostics"].append(error.diagnostic)
        return result

    def _digest(self, preview, target):
        choices = {
            line["source"]["ordinal"]: {k: v for k, v in line.items() if k != "source"}
            for line in preview["lines"]
        }
        journal = self.env["account.journal"].browse(preview["journal_id"])
        choices[0] = dict(
            mode=preview["mode"],
            accounting_date=str(preview["accounting_date"]),
            journal_id=journal.id,
            journal_write_date=str(journal.write_date),
        )
        return self.comparison.preview_digest(
            preview["document"], choices, target=target
        )

    def confirm(self, wizard):
        wizard.ensure_one()
        wizard.check_access("write")
        self.policy.check_access(wizard.incoming_document_id)
        if wizard.create_uid != self.env.user:
            raise AccessError(
                self.env._("Only the preview owner can confirm this operation.")
            )
        incoming = wizard.incoming_document_id
        existing = self.env[BINDING].search(
            [("incoming_document_id", "=", incoming.id)]
        )
        if existing:
            self.evidence.read(incoming)
            existing.move_id.check_access("read")
            return existing.move_id
        document = self.evidence.read(incoming)
        self.policy.check_eligibility(incoming, document)
        partner = self.policy.resolve_supplier(
            self.env.company, document["identity"]["issuer_vat"]
        )
        self._lock_suppliers(partner)
        self.env.cr.execute(
            SQL(
                ("SELECT id FROM l10n_cl_incoming_dte WHERE id = %s FOR UPDATE"),
                incoming.id,
            )
        )
        if wizard.existing_move_id:
            wizard.existing_move_id.check_access("write")
            if wizard.existing_move_id.company_id != incoming.company_id:
                raise AccessError(
                    self.env._("The target bill must belong to the source company.")
                )
            self.env.cr.execute(
                SQL(
                    "SELECT id FROM account_move WHERE id = %s FOR UPDATE",
                    wizard.existing_move_id.id,
                )
            )
        overrides = wizard._prepare_overrides()
        preview = self.prepare(
            incoming,
            overrides,
            wizard.journal_id,
            wizard.accounting_date,
            wizard.mode,
            wizard.existing_move_id,
        )
        for item in preview["diagnostics"]:
            if item["severity"] == "blocker":
                raise IncomingAccountError(item)
        if not wizard.preview_digest or wizard.preview_digest != preview.get(
            "preview_digest"
        ):
            reject(self.env, "stale_preview")
        with self.env.cr.savepoint():
            if wizard.mode == "create":
                values = self._prepare_move_values(
                    document,
                    partner,
                    wizard.journal_id,
                    preview["lines"],
                    wizard.accounting_date,
                )
                # Capability authorizes only provenance fields; native
                # invoice ACLs still apply.
                move = (
                    self.env["account.move"]
                    .with_context(**internal_context())
                    .create(values)
                    .with_env(self.env)
                )
                line_map = {
                    line.dte_inbox_line_number: line.id
                    for line in move.invoice_line_ids.filtered(
                        lambda detail: detail.display_type == "product"
                    )
                }
            else:
                move = wizard.existing_move_id
                move.check_access("write")
                if move.state != "draft":
                    reject(self.env, "existing_bill_incompatible")
                line_map = {
                    line["source"]["ordinal"]: line["existing_line_id"]
                    for line in preview["lines"]
                }
            self.comparison.compare_move(document, move, line_map)
            if move.state != "draft" or move.auto_post != "no":
                reject(self.env, "existing_bill_incompatible")
            for ordinal, line_id in line_map.items():
                self.env["account.move.line"].browse(line_id).with_context(
                    **internal_context()
                ).write({"dte_inbox_line_number": ordinal})
            self._create_binding(
                incoming, move, document, preview["lines"], wizard.mode
            )
            return move

    def _lock_suppliers(self, partners):
        if not partners:
            return
        partners.check_access("read")
        roots = partners.commercial_partner_id
        keys = {vat(p.vat) for p in roots if vat(p.vat)}
        # Only existence/locking of equivalent roots is privileged; no data is returned.
        equivalent = (
            self.env["res.partner"]
            .sudo()
            .with_context(active_test=False)
            .search(
                [
                    ("parent_id", "=", False),
                    ("vat", "!=", False),
                    ("company_id", "in", [False, *partners.env.companies.ids]),
                ]
            )
            .filtered(lambda p: vat(p.vat) in keys)
        )
        ids = sorted(set(roots.ids + equivalent.ids))
        self.env["res.partner"].flush_model(["dte_inbox_account_lock_revision"])
        self.env.cr.execute(
            SQL(
                "SELECT id FROM res_partner WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
                ids,
            )
        )
        # Updating the locked row makes stale REPEATABLE READ snapshots retry,
        # not continue stale.
        self.env.cr.execute(
            SQL(
                (
                    "UPDATE res_partner SET dte_inbox_account_lock_revision = "
                    "COALESCE(dte_inbox_account_lock_revision, 0) + 1 WHERE id = "
                    "ANY(%s)"
                ),
                ids,
            )
        )
        self.env["res.partner"].browse(ids).invalidate_recordset(
            ["dte_inbox_account_lock_revision"]
        )

    def _find_candidates(self, company, partner, document):
        partner.check_access("read")
        identity = document["identity"]
        candidates = (
            self.env["account.move"]
            .sudo()
            .with_context(active_test=False)
            .search(
                [
                    ("company_id", "=", company.id),
                    ("move_type", "=", "in_invoice"),
                    (
                        "l10n_latam_document_type_id.code",
                        "=",
                        identity["document_code"],
                    ),
                ]
            )
            .filtered(
                lambda m: (
                    vat(m.commercial_partner_id.vat) == identity["issuer_vat"]
                    and canonical_folio(m.l10n_latam_document_number)
                    == identity["folio"]
                )
            )
        )
        return self.env["account.move"].browse(candidates.ids)

    def _prepare_move_values(self, document, partner, journal, lines, accounting_date):
        identity = document["identity"]
        doc_type = self.policy.resolve_document_type(
            journal, partner, identity["document_code"]
        )
        details = []
        for mapped in lines:
            line = mapped["source"]
            details.append(
                Command.create(
                    dict(
                        display_type="product",
                        name="\n".join(
                            filter(None, [line["name"], line["description"]])
                        ),
                        dte_inbox_line_number=line["ordinal"],
                        product_id=mapped["product_id"],
                        product_uom_id=mapped["uom_id"],
                        account_id=mapped["account_id"],
                        tax_ids=[Command.set([mapped["tax_id"]])],
                        quantity=float(line["quantity"]),
                        price_unit=float(line["unit_price"]),
                        discount=float(line["discount_percent"]),
                    )
                )
            )
        values = dict(
            move_type="in_invoice",
            company_id=journal.company_id.id,
            partner_id=partner.id,
            journal_id=journal.id,
            currency_id=journal.company_id.currency_id.id,
            invoice_date=identity["issue_date"],
            date=accounting_date,
            auto_post="no",
            invoice_cash_rounding_id=False,
            l10n_latam_document_type_id=doc_type.id,
            l10n_latam_document_number=identity["folio"],
            ref=identity["folio"],
            invoice_line_ids=details,
        )
        if identity["due_date"]:
            values.update(
                invoice_date_due=identity["due_date"], invoice_payment_term_id=False
            )
        return values

    def _create_binding(self, incoming, move, document, mapping, operation):
        identity = document["identity"]
        snapshot = []
        for mapped in mapping:
            tax = self.env["account.tax"].browse(mapped["tax_id"])
            config = self.env["l10n_cl.incoming.dte.account.mapping"].browse(
                mapped["mapping_id"]
            )
            snapshot.append(
                dict(
                    ordinal=mapped["source"]["ordinal"],
                    mapping_id=config.id,
                    mapping_write_date=str(config.write_date) if config else False,
                    choices={k: v for k, v in mapped.items() if k != "source"},
                    tax=dict(
                        rate=tax.amount,
                        sii_code=tax.l10n_cl_sii_code,
                        price_include=tax.price_include,
                        company_id=tax.company_id.id,
                    ),
                    user_id=self.env.uid,
                )
            )
        values = dict(
            company_id=incoming.company_id.id,
            incoming_document_id=incoming.id,
            move_id=move.id,
            partner_id=move.commercial_partner_id.id,
            issuer_vat=identity["issuer_vat"],
            document_code=identity["document_code"],
            folio=identity["folio"],
            issue_date=identity["issue_date"],
            sii_environment=incoming.sii_environment,
            xml_sha256=incoming.xml_sha256,
            source_snapshot=source_snapshot(document),
            mapping_snapshot=snapshot,
            parser_version=document["version"],
            operation=operation,
            linked_by_id=self.env.uid,
        )
        # All source and invoice access checks precede this one immutable insertion.
        return (
            self.env[BINDING]
            .sudo()
            .with_context(**internal_context())
            .create(values)
            .with_env(self.env)
        )
