# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tools import SQL

from odoo.addons.l10n_cl_dte_inbox_account.services.binding_context import (
    internal_context,
)
from odoo.addons.l10n_cl_dte_inbox_account.services.binding_service import (
    IncomingAccountBindingService,
)
from odoo.addons.l10n_cl_dte_inbox_account.services.common import (
    BINDING,
    IncomingAccountError,
    canonical_folio,
    reject,
    source_snapshot,
    vat,
)
from odoo.addons.l10n_cl_dte_inbox_account.services.policy_service import (
    IncomingAccountPolicyService,
)

from .common import (
    PURCHASE_CODE,
    SALE_MOVE_TYPE,
    TOTAL_RETENTION_CODE,
    reject_received,
)
from .comparison_service import ReceivedPurchaseComparisonService
from .evidence_service import ReceivedPurchaseEvidenceService

CUSTOMER_CODES = {
    "supplier_missing": "customer_missing",
    "supplier_ambiguous": "customer_ambiguous",
    "supplier_archived": "customer_archived",
}


class ReceivedPurchaseService:
    """Bind a received DTE 46 to the customer invoice of our own sale."""

    def __init__(self, env):
        self.env = env
        self.policy = IncomingAccountPolicyService(env)
        self.evidence = ReceivedPurchaseEvidenceService(env)
        self.comparison = ReceivedPurchaseComparisonService(env)
        self.binding = IncomingAccountBindingService(env)

    # Eligibility -------------------------------------------------------

    def check_eligibility(self, incoming, document):
        self.policy.check_access(incoming)
        if incoming.state != "reviewed":
            reject(self.env, "source_not_reviewed")
        if incoming.xml_validation_state != "validated" or any(
            incoming[key] != "valid"
            for key in ("schema_state", "signature_state", "ted_state")
        ):
            reject(self.env, "source_validation_failed")
        company = incoming.company_id
        identity = document["identity"]
        # We are the seller: the buyer issued the DTE 46 to our RUT.
        if identity["receiver_vat"] != vat(company.vat):
            reject(self.env, "identity_mismatch")
        if identity["document_code"] != PURCHASE_CODE or document["currency"] != "CLP":
            reject(self.env, "unsupported_feature")
        if incoming.sii_environment != company.dte_sii_environment:
            reject(self.env, "environment_mismatch")
        config = company._get_dte_received46_configuration()
        mapped = company._dte_received46_partial_taxes()
        for item in document["totals"]["retentions"]:
            if item["code"] != TOTAL_RETENTION_CODE and item["code"] not in mapped:
                reject_received(
                    self.env, "retention_unmapped", source_value=item["code"]
                )
        return config

    def resolve_customer(self, company, issuer_vat):
        try:
            return self.policy.resolve_supplier(company, issuer_vat)
        except IncomingAccountError as error:
            code = CUSTOMER_CODES.get(error.diagnostic["code"])
            if not code:
                raise
            reject_received(self.env, code, source_value=issuer_vat)

    # Preview -----------------------------------------------------------

    def prepare(self, incoming, overrides=None, journal=None, accounting_date=None):
        self.policy.check_access(incoming)
        company = self.env.company
        result = dict(
            company_id=company.id,
            incoming_document_id=incoming.id,
            currency_id=company.currency_id.id,
            mode="create",
            journal_id=(journal or company.dte_inbox_account_received46_journal_id).id,
            accounting_date=accounting_date or fields.Date.context_today(incoming),
            partner_id=False,
            lines=[],
            diagnostics=[],
            candidates=[],
        )
        try:
            document = self.evidence.read(incoming)
            result["document"] = document
            result["diagnostics"].extend(document.get("information", []))
            config = self.check_eligibility(incoming, document)
            config = self._select_journal(config, result["journal_id"])
            result["journal_id"] = config["journal"].id
            partner = self.resolve_customer(company, document["identity"]["issuer_vat"])
            result["partner_id"] = partner.id
            self._check_dates_and_duplicates(document, partner, config, result)
            for source in document["lines"]:
                result["lines"].append(
                    self._map_line(source, (overrides or {}), config, result)
                )
            if not any(d["severity"] == "blocker" for d in result["diagnostics"]):
                self.comparison.preview(document, result["lines"], config)
                result["preview_digest"] = self._digest(result, config)
        except IncomingAccountError as error:
            result["diagnostics"].append(error.diagnostic)
        return result

    def _select_journal(self, config, journal_id):
        journal = self.env["account.journal"].browse(journal_id)
        if journal == config["journal"]:
            return config
        self.env.company._check_dte_received46_journal(journal)
        return dict(config, journal=journal)

    def resolve_document_type(self, journal, partner):
        journal.check_access("read")
        move = self.env["account.move"].new(
            dict(
                move_type=SALE_MOVE_TYPE,
                company_id=journal.company_id.id,
                journal_id=journal.id,
                partner_id=partner.id,
            )
        )
        types = self.env["l10n_latam.document.type"].search(
            move._get_l10n_latam_documents_domain() + [("code", "=", PURCHASE_CODE)]
        )
        if len(types) != 1:
            reject(self.env, "unsupported_feature")
        return types

    def _check_dates_and_duplicates(self, document, partner, config, result):
        company = self.env.company
        journal = config["journal"]
        accounting_date = fields.Date.to_date(result["accounting_date"])
        if accounting_date <= company._get_user_fiscal_lock_date(journal):
            reject(self.env, "unsupported_feature", source_value=accounting_date)
        candidates = self.find_candidates(company, document)
        result["candidates"] = candidates.ids
        if candidates or self.env[BINDING].sudo().search_count(
            [
                ("company_id", "=", company.id),
                ("issuer_vat", "=", document["identity"]["issuer_vat"]),
                ("document_code", "=", PURCHASE_CODE),
                ("folio", "=", document["identity"]["folio"]),
            ]
        ):
            reject_received(self.env, "duplicate_invoice")
        doc_type = self.resolve_document_type(journal, partner)
        name = "{} {}".format(
            doc_type.doc_code_prefix,
            doc_type._format_document_number(document["identity"]["folio"]),
        )
        # Existence only: the colliding entry may belong to any buyer.
        if (
            self.env["account.move"]
            .sudo()
            .search_count([("journal_id", "=", journal.id), ("name", "=", name)])
        ):
            reject_received(self.env, "journal_name_collision", source_value=name)

    def find_candidates(self, company, document):
        identity = document["identity"]
        # Existence of an equivalent sale is checked across records the
        # operator may not read; only ids of readable ones are returned.
        candidates = (
            self.env["account.move"]
            .sudo()
            .with_context(active_test=False)
            .search(
                [
                    ("company_id", "=", company.id),
                    ("move_type", "=", SALE_MOVE_TYPE),
                    ("l10n_latam_document_type_id.code", "=", PURCHASE_CODE),
                ]
            )
            .filtered(
                lambda move: (
                    vat(move.commercial_partner_id.vat) == identity["issuer_vat"]
                    and canonical_folio(move.l10n_latam_document_number)
                    == identity["folio"]
                )
            )
        )
        return self.env["account.move"].browse(candidates.ids)

    def _map_line(self, source, overrides, config, result):
        choice = overrides.get(source["ordinal"], {})
        default_tax = (
            config["vat_tax"].browse() if source["exempt"] else config["vat_tax"]
        )
        mapped = dict(
            source=source,
            mapping_id=False,
            product_id=choice.get("product_id", False),
            account_id=choice.get("account_id")
            or config["journal"].default_account_id.id,
            tax_id=choice.get("tax_id", default_tax.id),
            uom_id=choice.get("uom_id", False),
            existing_line_id=False,
        )
        try:
            self._validate_line(source, mapped, config)
        except IncomingAccountError as error:
            result["diagnostics"].append(error.diagnostic)
        return mapped

    def _validate_line(self, source, mapped, config):
        ordinal = source["ordinal"]
        company = config["journal"].company_id
        account = self.env["account.account"].browse(mapped["account_id"]).exists()
        if not account:
            reject(self.env, "mapping_missing", ordinal=ordinal)
        account.check_access("read")
        if (
            company not in account.company_ids
            or account.deprecated
            or account.account_type
            in {"asset_receivable", "liability_payable", "off_balance"}
        ):
            reject_received(self.env, "account_invalid", ordinal=ordinal)
        expected = config["vat_tax"].browse() if source["exempt"] else config["vat_tax"]
        if (mapped["tax_id"] or False) != (expected.id or False):
            reject_received(self.env, "tax_invalid", ordinal=ordinal)
        product = self.env["product.product"].browse(mapped["product_id"]).exists()
        if mapped["product_id"] and (
            not product
            or not product.active
            or product.company_id not in (company, self.env["res.company"])
        ):
            reject_received(self.env, "account_invalid", ordinal=ordinal)
        unit = self.env["uom.uom"].browse(mapped["uom_id"]).exists()
        if (mapped["uom_id"] and not unit) or (
            unit
            and (
                not unit.active
                or (product and unit.category_id != product.uom_id.category_id)
            )
        ):
            reject(self.env, "uom_unresolved", ordinal=ordinal)

    def _digest(self, preview, config):
        choices = {
            line["source"]["ordinal"]: {k: v for k, v in line.items() if k != "source"}
            for line in preview["lines"]
        }
        choices[0] = dict(
            mode=preview["mode"],
            accounting_date=str(preview["accounting_date"]),
            config=[
                [record._name, record.id, str(record.write_date)]
                for records in config.values()
                for record in records
            ],
        )
        return self.binding.comparison.preview_digest(preview["document"], choices)

    # Confirmation ------------------------------------------------------

    def confirm(self, wizard):
        wizard.ensure_one()
        wizard.check_access("write")
        incoming = wizard.incoming_document_id
        self.policy.check_access(incoming)
        if wizard.create_uid != self.env.user:
            raise AccessError(
                self.env._("Only the preview owner can confirm this operation.")
            )
        existing = self.env[BINDING].search(
            [("incoming_document_id", "=", incoming.id)]
        )
        if existing:
            self.evidence.read(incoming)
            existing.move_id.check_access("read")
            return existing.move_id
        if wizard.mode != "create":
            reject_received(self.env, "link_unsupported")
        document = self.evidence.read(incoming)
        config = self.check_eligibility(incoming, document)
        partner = self.resolve_customer(
            self.env.company, document["identity"]["issuer_vat"]
        )
        self.binding._lock_suppliers(partner)
        self.env.cr.execute(
            SQL(
                "SELECT id FROM l10n_cl_incoming_dte WHERE id = %s FOR UPDATE",
                incoming.id,
            )
        )
        preview = self.prepare(
            incoming,
            wizard._prepare_overrides(),
            wizard.journal_id,
            wizard.accounting_date,
        )
        for item in preview["diagnostics"]:
            if item["severity"] == "blocker":
                raise IncomingAccountError(item)
        if not wizard.preview_digest or wizard.preview_digest != preview.get(
            "preview_digest"
        ):
            reject(self.env, "stale_preview")
        config = self._select_journal(config, preview["journal_id"])
        with self.env.cr.savepoint():
            return self._create_invoice(incoming, document, partner, config, preview)

    def _create_invoice(self, incoming, document, partner, config, preview):
        values = self._prepare_move_values(
            document, partner, config, preview["lines"], preview["accounting_date"]
        )
        # The capability authorizes only provenance fields; native invoice
        # ACLs still apply.
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
        self.comparison.compare_move(document, move, config, line_map)
        if move.state != "draft" or move.auto_post != "no":
            reject_received(self.env, "invoice_incompatible")
        self.binding._create_binding(
            incoming, move, document, preview["lines"], "create"
        )
        return move

    def _prepare_move_values(self, document, partner, config, lines, accounting_date):
        identity = document["identity"]
        journal = config["journal"]
        doc_type = self.resolve_document_type(journal, partner)
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
                        tax_ids=[
                            Command.set(self.comparison.line_taxes(line, config).ids)
                        ],
                        quantity=float(line["quantity"]),
                        price_unit=float(line["unit_price"]),
                        discount=float(line["discount_percent"]),
                    )
                )
            )
        values = dict(
            move_type=SALE_MOVE_TYPE,
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

    # Posting -----------------------------------------------------------

    def check_posting(self, move):
        """Re-prove a bound draft against its immutable evidence before posting."""
        binding = self.env[BINDING].search([("move_id", "=", move.id)])
        binding.ensure_one()
        incoming = binding.incoming_document_id
        document = self.evidence.read(incoming)
        if binding.xml_sha256 != document[
            "xml_sha256"
        ] or binding.source_snapshot != source_snapshot(document):
            reject(self.env, "source_digest_mismatch")
        config = self.check_eligibility(incoming, document)
        config = self._select_journal(config, move.journal_id.id)
        self.comparison.compare_move(document, move, config)
