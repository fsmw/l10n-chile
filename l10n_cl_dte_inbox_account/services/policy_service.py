# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError

from .common import GROUP, reject, vat


class IncomingAccountPolicyService:
    def __init__(self, env):
        self.env = env

    def check_access(self, incoming):
        incoming.ensure_one()
        if not self.env.user.has_group(GROUP):
            raise AccessError(
                self.env._("The incoming DTE vendor bill role is required.")
            )
        incoming.check_access("read")
        if (
            incoming.company_id != self.env.company
            or self.env.company not in self.env.user.company_ids
        ):
            raise AccessError(
                self.env._("Use the source company as your active allowed company.")
            )
        self.env["account.move"].check_access("create")
        self.env["account.move"].check_access("write")

    def check_eligibility(self, incoming, document):
        self.check_access(incoming)
        if incoming.state != "reviewed":
            reject(self.env, "source_not_reviewed")
        if incoming.xml_validation_state != "validated" or any(
            getattr(incoming, k) != "valid"
            for k in ("schema_state", "signature_state", "ted_state")
        ):
            reject(self.env, "source_validation_failed")
        company = incoming.company_id
        company._check_dte_inbox_account_configuration()
        if not company.dte_inbox_account_enabled:
            reject(self.env, "company_disabled")
        if document["identity"]["receiver_vat"] != vat(company.vat):
            reject(self.env, "identity_mismatch")
        if (
            document["identity"]["document_code"] not in {"33", "34"}
            or document["currency"] != "CLP"
        ):
            reject(self.env, "unsupported_feature")
        if incoming.sii_environment != company.dte_sii_environment:
            reject(self.env, "environment_mismatch")

    def resolve_supplier(self, company, issuer_vat):
        normalized = vat(issuer_vat)
        caf = self.env["l10n_cl.caf.authorization"]
        if not caf._is_valid_vat(normalized):
            reject(self.env, "supplier_missing")
        partners = (
            self.env["res.partner"]
            .with_context(active_test=False)
            .search(
                [
                    ("company_id", "in", [False, company.id]),
                    ("vat", "!=", False),
                ]
            )
            .filtered(lambda p: vat(p.vat) == normalized)
            .commercial_partner_id
        )
        if not partners:
            reject(self.env, "supplier_missing")
        if len(partners) != 1:
            reject(self.env, "supplier_ambiguous")
        if not partners.active:
            reject(self.env, "supplier_archived")
        if vat(partners.vat) != normalized or partners.country_id.code not in (
            False,
            "CL",
        ):
            reject(self.env, "supplier_missing")
        partners.check_access("read")
        return partners

    def resolve_document_type(self, journal, partner, document_code):
        if (
            journal.company_id != self.env.company
            or not journal.active
            or journal.type != "purchase"
            or not journal.l10n_latam_use_documents
        ):
            reject(self.env, "company_disabled")
        journal.check_access("read")
        move = self.env["account.move"].new(
            dict(
                move_type="in_invoice",
                company_id=journal.company_id.id,
                journal_id=journal.id,
                partner_id=partner.id,
            )
        )
        types = self.env["l10n_latam.document.type"].search(
            move._get_l10n_latam_documents_domain() + [("code", "=", document_code)]
        )
        if len(types) != 1:
            reject(self.env, "unsupported_feature")
        return types
