# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import Command
from odoo.tests import new_test_user

from odoo.addons.l10n_cl_dte_inbox_account.tests.common import IncomingAccountCase
from odoo.addons.l10n_cl_dte_rcv.services.rcv_normalizer import RcvNormalizer
from odoo.addons.l10n_cl_dte_rcv.tests.common import RcvMatchingTestCase

# Synthetic RUTs with valid check digits; they do not identify anyone.
HEADER_SUPPLIER_RUT = "76222222-1"
UNKNOWN_SUPPLIER_RUT = "77333333-5"
ISSUE_DATE = "2026-01-15"
PERIOD_YEAR, PERIOD_MONTH = 2026, 1


class RcvInboxCase(IncomingAccountCase):
    """Signed synthetic received DTEs and RCV reads of the receiving company."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env(su=True)
        groups = (
            "base.group_user,l10n_cl_dte_core.group_dte_manager,"
            "l10n_cl_dte_inbox_account.group_inbox_account_user,"
            "base.group_partner_manager"
        )
        cls.rcv_manager = new_test_user(
            env,
            login="rcv_inbox_manager",
            groups=groups,
            company_id=cls.recipient.id,
            company_ids=[Command.set([cls.recipient.id])],
            context={"no_reset_password": True},
        )
        cls.rcv_reader = new_test_user(
            env,
            login="rcv_inbox_reader",
            groups="base.group_user,l10n_cl_dte_core.group_dte_user",
            company_id=cls.recipient.id,
            company_ids=[Command.set([cls.recipient.id])],
            context={"no_reset_password": True},
        )
        cls.other_manager = new_test_user(
            env,
            login="rcv_inbox_other_manager",
            groups=groups,
            company_id=cls.company.id,
            company_ids=[Command.set([cls.company.id])],
            context={"no_reset_password": True},
        )
        cls.header_supplier = env["res.partner"].create(
            {
                "name": "Synthetic Header Supplier",
                "is_company": True,
                "vat": HEADER_SUPPLIER_RUT,
                "country_id": env.ref("base.cl").id,
                "l10n_latam_identification_type_id": env.ref("l10n_cl.it_RUT").id,
                "l10n_cl_sii_taxpayer_type": "1",
            }
        )

    def setUp(self):
        super().setUp()
        self.rcv_env = self.env(
            user=self.rcv_manager,
            context=dict(self.env.context, allowed_company_ids=[self.recipient.id]),
        )

    # Received DTEs

    def received(self, reviewed=True):
        """Register a signed synthetic DTE of the supplier in the inbox."""
        source = self.source(reviewed=reviewed)
        self.map_supplier(source)
        return source

    def map_supplier(self, source):
        """Map every detail of the supplier to the expense account and VAT."""
        mapping = self.env["l10n_cl.incoming.dte.account.mapping"].sudo()
        if mapping.search_count([("partner_id", "=", self.supplier.id)]):
            return
        unit = self.document(source)["lines"][0]["unit"]
        self.mapping(uom_id=self.unit.id, source_uom=unit or False)

    # RCV reads

    def row(self, folio, rut=None, net=1000, vat_amount=190, **kwargs):
        kwargs.setdefault("date", ISSUE_DATE)
        return RcvMatchingTestCase._row(
            folio, rut or self.supplier.vat, net=net, vat_amount=vat_amount, **kwargs
        )

    def read_rcv(self, rows, env=None):
        """Store ``rows`` as a complete SII purchase read of January 2026."""
        env = env or self.rcv_env
        values = {
            "company_id": self.recipient.id,
            "year": PERIOD_YEAR,
            "month": PERIOD_MONTH,
            "operation": "purchase",
        }
        periods = env["l10n_cl.dte.rcv.period"]
        period = periods.search(
            [(key, "=", value) for key, value in values.items()]
        ) or periods.create(values)
        sync = env["l10n_cl.dte.rcv.sync"].create(
            {
                "period_id": period.id,
                "company_id": self.recipient.id,
                "operation": "purchase",
            }
        )
        RcvNormalizer(env).apply(sync, [RcvMatchingTestCase._section(rows)])
        return sync

    def rcv_document(self, folio, rut=None, **kwargs):
        sync = self.read_rcv([self.row(folio, rut, **kwargs)])
        return sync.document_ids.filtered(lambda document: document.folio == str(folio))

    def bound_bill(self, source, accounting_date=ISSUE_DATE):
        """Bind a vendor bill to ``source`` through the inbox preview."""
        wizard = self.wizard(source, ready=False)
        wizard.write({"accounting_date": accounting_date})
        wizard.action_refresh_preview()
        self.assertFalse(wizard.has_blockers, wizard.diagnostic_text)
        return self.bill(wizard)

    def enable_auto_drafts(self, company=None, user=None):
        (company or self.recipient).sudo().write(
            {
                "dte_rcv_auto_draft": True,
                "dte_rcv_draft_user_id": (user or self.rcv_manager).id,
            }
        )
