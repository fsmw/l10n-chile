# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Prepare vendor bill drafts for RCV purchases missing in accounting.

When the inbox holds the received DTE, the draft is built from its signed XML
through the vendor bill binding of ``l10n_cl_dte_inbox_account``. Otherwise it
is built from the RCV amounts, one line per VAT component. Drafts are never
posted.
"""

import calendar
import logging
from datetime import date

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import SQL

from odoo.addons.l10n_cl_dte_inbox_account.services.binding_service import (
    IncomingAccountBindingService,
)
from odoo.addons.l10n_cl_dte_inbox_account.services.common import (
    VENDOR_MOVE_TYPES,
    canonical_folio,
    vat,
)
from odoo.addons.l10n_cl_dte_rcv.services.rcv_candidates import (
    RcvCandidateFinder,
    partner_vat,
)
from odoo.addons.l10n_cl_dte_rcv.services.rcv_fiscal_evidence import (
    PURCHASE_VAT_COMPONENTS,
    RcvFiscalEvidence,
)
from odoo.addons.l10n_cl_dte_rcv.services.rcv_matcher import RcvMatcher
from odoo.addons.l10n_cl_dte_rcv.services.rcv_values import (
    is_valid_rut,
    normalize_vat,
)

_logger = logging.getLogger(__name__)

MANAGER_GROUP = "l10n_cl_dte_core.group_dte_manager"
BILL_WIZARD = "l10n_cl.incoming.dte.bill.wizard"
MISSING_STATE = "missing"
VAT_COMPONENTS = tuple(field for field, _tag, _expr in PURCHASE_VAT_COMPONENTS)
EXEMPT_COMPONENT = "exempt"
NET_COMPONENT = "net"
DRAFT_ERRORS = (UserError, ValidationError, AccessError)
MAX_ERROR_LENGTH = 500


def rcv_accounting_date(document):
    """Return the accounting date of a draft within the RCV tax period.

    The document date is kept when it falls in the period; otherwise the
    draft is dated the last day of the period, where the RCV declares it.
    """
    period = document.tax_period or ""
    if len(period) != 6 or not period.isdigit():
        return False
    year, month = int(period[:4]), int(period[4:])
    if not 1 <= month <= 12:
        return False
    issued = document.document_date
    if issued and issued.strftime("%Y%m") == period:
        return issued
    return date(year, month, calendar.monthrange(year, month)[1])


class RcvDraftService:
    """Create the vendor bill draft of one RCV purchase document."""

    def __init__(self, env):
        self.env = env
        self.finder = RcvCandidateFinder(env)

    # Guards

    def check_allowed(self, document):
        env = self.env
        if not env.user.has_group(MANAGER_GROUP):
            raise AccessError(
                env._("Only a DTE manager can prepare vendor bill drafts from the RCV.")
            )
        if document.company_id - env.companies:
            raise UserError(env._("Select the company of the RCV document."))
        document.check_access("write")
        env["account.move"].check_access("create")

    def check_eligible(self, document):
        """Refuse a document that is not a purchase missing in accounting."""
        env = self.env
        document.ensure_one()
        if (
            document.operation != "purchase"
            or document.document_type_code not in VENDOR_MOVE_TYPES
        ):
            raise UserError(
                env._(
                    "Drafts are only prepared for the invoices, debit notes and "
                    "credit notes of the purchase register."
                )
            )
        if document.reconciliation_state != MISSING_STATE or document.sudo().move_id:
            raise UserError(
                env._(
                    "Only an RCV purchase missing in accounting gets a draft; "
                    "reconcile the document first."
                )
            )
        if document._get_rcv_candidate_moves():
            raise UserError(
                env._(
                    "A journal entry with the document type, folio and RUT of this "
                    "document already exists; reconcile the document instead."
                )
            )

    def lock(self, document):
        """Serialize the drafts of one fiscal identity until the commit."""
        identity = "|".join(
            (
                "l10n_cl_dte_rcv_inbox",
                str(document.company_id.id),
                vat(document.issuer_vat),
                document.document_type_code or "",
                canonical_folio(document.folio) or (document.folio or ""),
            )
        )
        self.env.cr.execute(SQL("SELECT pg_advisory_xact_lock(hashtext(%s))", identity))
        document.invalidate_recordset()

    # Supplier

    def partner_candidates(self, document):
        return self.finder._commercial_partners(partner_vat(document))

    def existing_partner(self, document, partner=None):
        """Return the supplier with the RUT of ``document``, if there is one."""
        env = self.env
        candidates = self.partner_candidates(document)
        if partner and partner not in candidates:
            raise UserError(
                env._("The selected supplier does not have the RUT of the document.")
            )
        if not partner and len(candidates) > 1:
            raise UserError(
                env._(
                    "Several suppliers have the RUT %s; select one.",
                    document.issuer_vat,
                )
            )
        partner = partner or candidates
        if partner and not partner.active:
            raise UserError(
                env._("The supplier with the RUT %s is archived.", document.issuer_vat)
            )
        return partner

    def resolve_partner(self, document, partner=None, create=False, name=""):
        """Return the supplier; a new one is created only when requested."""
        partner = self.existing_partner(document, partner)
        if partner:
            return partner
        if not create:
            raise UserError(
                self.env._(
                    "No supplier has the RUT %s. Confirm the creation of the "
                    "supplier, or create it first.",
                    document.issuer_vat,
                )
            )
        return self._create_partner(document, name)

    def _create_partner(self, document, name):
        env = self.env
        name = (name or "").strip()
        rut = normalize_vat(document.issuer_vat)
        if not name:
            raise UserError(env._("Enter the name of the new supplier."))
        if not is_valid_rut(rut):
            raise UserError(env._("The RUT %s of the document is not valid.", rut))
        rut_type = env.ref("l10n_cl.it_RUT", raise_if_not_found=False)
        return env["res.partner"].create(
            {
                "name": name,
                "vat": rut,
                "is_company": True,
                "country_id": env.ref("base.cl").id,
                "l10n_latam_identification_type_id": rut_type.id if rut_type else False,
                # The RCV registers DTEs it issued: it is a VAT taxpayer.
                "l10n_cl_sii_taxpayer_type": "1",
                "supplier_rank": 1,
            }
        )

    # Journal, account and taxes

    def journal(self, company):
        journal = company.dte_inbox_account_journal_id
        if not journal:
            journal = self.env["account.journal"].search(
                [
                    ("company_id", "=", company.id),
                    ("type", "=", "purchase"),
                    ("l10n_latam_use_documents", "=", True),
                ],
                limit=1,
            )
        if not journal:
            raise UserError(
                self.env._("Configure a purchase journal that uses documents.")
            )
        return journal

    def default_account(self, partner, journal):
        """Return the usual expense account of the supplier, or of the journal."""
        company = journal.company_id
        accounts = self.env["account.account"].with_company(company)
        frequent = (
            accounts._get_most_frequent_account_for_partner(
                company.id, partner.id, move_type="in_invoice", journal_id=journal.id
            )
            if partner
            else False
        )
        account = accounts.browse(frequent).exists() if frequent else accounts
        return account or journal.default_account_id

    def accounting_date(self, document, journal):
        """Return the RCV period date, unless the period is locked."""
        target = rcv_accounting_date(document)
        company = journal.company_id
        if target and target <= company._get_user_fiscal_lock_date(journal):
            return False
        return target

    def component_tax(self, company, component):
        """Return the one purchase tax tagged for a VAT component, if any."""
        field_name, tag_xmlid, expression_xmlid = component
        tags = RcvFiscalEvidence(self.env).component_tags(tag_xmlid, expression_xmlid)
        taxes = self.env["account.tax"]
        if tags:
            taxes = taxes.search(
                [
                    ("company_id", "=", company.id),
                    ("type_tax_use", "=", "purchase"),
                    ("amount_type", "=", "percent"),
                    ("amount", ">", 0),
                    ("invoice_repartition_line_ids.tag_ids", "in", tags.ids),
                ]
            )
        if len(taxes) == 1:
            return taxes
        if field_name == "vat_amount":
            return company.dte_inbox_account_vat_tax_id
        return self.env["account.tax"]

    # Lines from the RCV amounts

    def summary_lines(self, document, account):
        """Return the draft lines that book the RCV amounts of ``document``."""
        env, currency = self.env, document.currency_id
        lines = []
        if not currency.is_zero(document.net_amount):
            lines += self._net_lines(document, account)
        if not currency.is_zero(document.exempt_amount):
            lines.append(
                self._line(
                    env._("Exempt amount per the RCV"),
                    document.exempt_amount,
                    account,
                    env["account.tax"],
                    EXEMPT_COMPONENT,
                )
            )
        if not lines:
            raise UserError(env._("The RCV document has no amount to book."))
        return lines

    def _net_lines(self, document, account):
        """Split the net amount by VAT component when the RCV allows it."""
        env, currency = self.env, document.currency_id
        active = [
            component
            for component in PURCHASE_VAT_COMPONENTS
            if not currency.is_zero(document[component[0]])
        ]
        unresolved = [
            self._line(
                env._("Net amount per the RCV"),
                document.net_amount,
                account,
                env["account.tax"],
                NET_COMPONENT,
            )
        ]
        if not active:
            return unresolved
        taxes = [self.component_tax(document.company_id, item) for item in active]
        if len(active) == 1:
            field_name = active[0][0]
            label = self._label(field_name)
            return [
                self._line(label, document.net_amount, account, taxes[0], field_name)
            ]
        if not all(tax.amount for tax in taxes):
            return unresolved
        lines = [
            self._line(
                self._label(item[0]),
                currency.round(document[item[0]] * 100.0 / tax.amount),
                account,
                tax,
                item[0],
            )
            for item, tax in zip(active, taxes, strict=True)
        ]
        base = sum(line["price_unit"] for line in lines)
        if not currency.is_zero(base - document.net_amount):
            return unresolved
        return lines

    def _label(self, component):
        env = self.env
        return {
            "vat_amount": env._("Net amount with recoverable VAT per the RCV"),
            "vat_not_recoverable": env._(
                "Net amount with non-recoverable VAT per the RCV"
            ),
            "common_vat": env._("Net amount with common use VAT per the RCV"),
        }[component]

    @staticmethod
    def _line(name, amount, account, taxes, component):
        return {
            "name": name,
            "price_unit": amount,
            "account_id": account.id,
            "tax_ids": taxes.ids,
            "component": component,
        }

    def check_lines(self, lines, automatic=False):
        """Refuse lines whose account or tax treatment is not resolved."""
        env = self.env
        if not lines:
            raise UserError(env._("Add the lines of the draft."))
        for line in lines:
            name, taxed = line["name"], bool(line["tax_ids"])
            if not line["account_id"]:
                raise UserError(env._("Choose the account of the line %s.", name))
            if line["component"] == EXEMPT_COMPONENT and taxed:
                raise UserError(env._("Remove the taxes of the exempt line %s.", name))
            if line["component"] in VAT_COMPONENTS and not taxed:
                raise UserError(env._("Choose the VAT of the line %s.", name))
            if automatic and line["component"] == NET_COMPONENT and not taxed:
                raise UserError(
                    env._(
                        "The RCV does not identify the VAT of the net amount; "
                        "prepare this draft manually."
                    )
                )

    # Drafts

    def create_summary_draft(self, document, partner, journal, lines):
        """Create a draft that books the RCV amounts of ``document``."""
        env = self.env
        code = document.document_type_code
        document_type = env["l10n_latam.document.type"].search(
            [("code", "=", code), ("country_id.code", "=", "CL")], limit=1
        )
        if not document_type:
            raise UserError(env._("The DTE type %s is not configured.", code))
        values = {
            "move_type": VENDOR_MOVE_TYPES[code],
            "company_id": document.company_id.id,
            "partner_id": partner.id,
            "journal_id": journal.id,
            "invoice_date": document.document_date,
            "l10n_latam_document_type_id": document_type.id,
            "l10n_latam_document_number": canonical_folio(document.folio)
            or document.folio,
            "auto_post": "no",
            "invoice_line_ids": [
                Command.create(
                    {
                        "name": line["name"],
                        "quantity": 1,
                        "price_unit": line["price_unit"],
                        "account_id": line["account_id"],
                        "tax_ids": [Command.set(line["tax_ids"])],
                    }
                )
                for line in lines
            ],
        }
        accounting_date = self.accounting_date(document, journal)
        if accounting_date:
            values["date"] = accounting_date
        return env["account.move"].with_company(document.company_id).create(values)

    def xml_wizard(self, document, incoming, journal):
        """Return the vendor bill preview of the received DTE of ``document``."""
        env = self.env["res.company"].with_company(document.company_id).env
        preview = IncomingAccountBindingService(env).prepare(
            incoming.with_env(env),
            journal=journal.with_env(env),
            accounting_date=self.accounting_date(document, journal) or None,
        )
        wizards = env[BILL_WIZARD]
        return wizards.create(wizards._preview_values(preview))

    def create_xml_draft(self, document, incoming, journal):
        """Create the draft from the signed XML, without user choices."""
        wizard = self.xml_wizard(document, incoming, journal)
        if wizard.has_blockers:
            raise UserError(
                self.env._(
                    "The received DTE cannot be prepared as a vendor bill:\n%s",
                    wizard.diagnostic_text,
                )
            )
        move = IncomingAccountBindingService(wizard.env).confirm(wizard)
        return move.with_env(self.env)

    def finish(self, document, move, reason):
        """Record the draft and reconcile the document with it."""
        document._create_manual_log("draft_created", move, reason)
        RcvMatcher(self.env).reconcile(document)
        if document.dte_rcv_draft_error:
            document.dte_rcv_draft_error = False
        return move

    # Automatic drafts

    def create_automatic_draft(self, document):
        """Create the draft of one document; return the move or False.

        A failure is recorded on the document and never stops the others.
        """
        try:
            with self.env.cr.savepoint():
                return self._automatic_draft(document)
        except DRAFT_ERRORS as error:
            message = str(error.args[0] if error.args else error)
        _logger.info("RCV document %s: no automatic draft.", document.id)
        document.write({"dte_rcv_draft_error": message[:MAX_ERROR_LENGTH]})
        return False

    def _automatic_draft(self, document):
        env = self.env
        self.lock(document)
        if document.reconciliation_state != MISSING_STATE:
            return False
        if document.sudo().move_id or document._get_rcv_candidate_moves():
            # An entry was booked since the last reconciliation.
            RcvMatcher(env).reconcile(document)
            return False
        self.check_eligible(document)
        journal = self.journal(document.company_id)
        partner = self.existing_partner(document)
        if not partner:
            raise UserError(
                env._(
                    "No supplier has the RUT %s; automatic drafts never create "
                    "suppliers.",
                    document.issuer_vat,
                )
            )
        incoming = document._link_rcv_incoming_dte()
        if incoming:
            move = self.create_xml_draft(document, incoming, journal)
            reason = env._("Automatic draft from the signed XML of the received DTE.")
        else:
            account = self.default_account(partner, journal)
            lines = self.summary_lines(document, account)
            self.check_lines(lines, automatic=True)
            move = self.create_summary_draft(document, partner, journal, lines)
            reason = env._("Automatic draft from the RCV amounts.")
        return self.finish(document, move, reason)
