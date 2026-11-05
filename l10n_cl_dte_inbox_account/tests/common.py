# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).


from lxml import etree

from odoo import Command
from odoo.tests import new_test_user

from odoo.addons.l10n_cl_dte_inbox.services.incoming_validation import xml_bytes
from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import DteXmlComposer
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase

from ..services.common import MAPPING, IncomingAccountError
from ..services.evidence_service import IncomingAccountEvidenceService

NS = {"s": "http://www.sii.cl/SiiDte"}


class IncomingAccountCase(DteInvoiceTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        original_env = cls.env
        cls.env = cls.env(su=True)
        cls.receiver.sudo().write(
            {"vat": "76234567-6", "l10n_cl_sii_taxpayer_type": "1"}
        )
        cls.recipient_data = cls.setup_other_company(
            name="Synthetic incoming accounting",
            partner_id=cls.receiver.id,
            country_id=cls.chile.id,
            currency_id=cls.env.ref("base.CLP").id,
        )
        cls.recipient = cls.recipient_data["company"]
        cls.recipient.write(
            {"dte_sii_environment": "demo", "account_fiscal_country_id": cls.chile.id}
        )
        cls.recipient.currency_id.rounding = 1
        cls.supplier = cls.company.partner_id
        cls.supplier.write({"l10n_cl_sii_taxpayer_type": "1"})
        cls.purchase_journal = cls.recipient_data["default_journal_purchase"]
        cls.purchase_journal.write({"l10n_latam_use_documents": True})
        cls.expense = cls.recipient_data["default_account_expense"]
        group = cls.recipient_data["default_tax_purchase"].tax_group_id
        cls.purchase_vat = cls.env["account.tax"].create(
            dict(
                name="Synthetic purchase VAT",
                amount=19,
                amount_type="percent",
                type_tax_use="purchase",
                l10n_cl_sii_code=14,
                company_id=cls.recipient.id,
                tax_group_id=group.id,
                price_include_override="tax_excluded",
            )
        )
        cls.purchase_exempt = cls.purchase_vat.copy(
            dict(name="Synthetic exempt purchase", amount=0, l10n_cl_sii_code=0)
        )
        cls.recipient.write(
            dict(
                dte_inbox_account_journal_id=cls.purchase_journal.id,
                dte_inbox_account_vat_tax_id=cls.purchase_vat.id,
                dte_inbox_account_exempt_tax_id=cls.purchase_exempt.id,
                dte_inbox_account_enabled=True,
            )
        )
        cls.dte_manager.write({"company_ids": [Command.link(cls.recipient.id)]})
        cls.operator = new_test_user(
            cls.env,
            login="incoming_account_operator",
            groups="base.group_user,l10n_cl_dte_inbox_account.group_inbox_account_user",
            company_id=cls.recipient.id,
            company_ids=[Command.set([cls.recipient.id])],
            context={"no_reset_password": True},
        )
        cls.manager = new_test_user(
            cls.env,
            login="incoming_account_manager",
            groups=(
                "base.group_user,l10n_cl_dte_inbox_account.group_inbox_account_manager"
            ),
            company_id=cls.recipient.id,
            company_ids=[Command.set([cls.recipient.id])],
            context={"no_reset_password": True},
        )
        cls.reader = new_test_user(
            cls.env,
            login="incoming_account_reader",
            groups="base.group_user,l10n_cl_dte_core.group_dte_user",
            company_id=cls.recipient.id,
            company_ids=[Command.set([cls.recipient.id])],
            context={"no_reset_password": True},
        )
        cls.billing = new_test_user(
            cls.env,
            login="incoming_account_billing",
            groups="base.group_user,account.group_account_invoice",
            company_id=cls.recipient.id,
            company_ids=[Command.set([cls.recipient.id])],
            context={"no_reset_password": True},
        )
        cls.env = original_env

    def setUp(self):
        super().setUp()
        self.create_signing_profile()
        self.op_env = self.env(
            user=self.operator,
            context=dict(self.env.context, allowed_company_ids=[self.recipient.id]),
        )
        self.manager_env = self.env(
            user=self.manager,
            context=dict(self.env.context, allowed_company_ids=[self.recipient.id]),
        )
        self.unit = self.env.ref("uom.product_uom_unit")
        self.counter = 120000

    def signed(self, code="33", mixed=False, transform=None):
        invoice = self.make_invoice(code=code, post=False, taxable=code == "33")
        invoice.invoice_line_ids.write(
            {"quantity": 1, "price_unit": 1000, "discount": 0}
        )
        if mixed:
            invoice.write(
                {
                    "invoice_line_ids": [
                        Command.create(
                            dict(
                                name="Exempt detail",
                                quantity=1,
                                price_unit=200,
                                account_id=self.company_data[
                                    "default_account_revenue"
                                ].id,
                                tax_ids=[Command.clear()],
                            )
                        )
                    ]
                }
            )
        invoice.action_post()
        self.counter += 10
        snapshot, caf = self.capture(invoice, code=code, folio=self.counter)
        composer = DteXmlComposer()
        snapshot["ted_xml"] = composer.compose_ted(snapshot, caf)
        unsigned = composer.compose_unsigned_document(snapshot)
        if transform:
            root = etree.fromstring(unsigned)
            transform(root)
            unsigned = xml_bytes(root)
        return SignatureService().sign_document(
            self.company.with_user(self.dte_manager), "demo", unsigned
        )

    def source(self, code="33", mixed=False, transform=None, reviewed=True):
        content = self.signed(code=code, mixed=mixed, transform=transform)
        incoming = (
            self.env["l10n_cl.incoming.dte"]
            .with_user(self.dte_manager)
            .with_company(self.recipient)
            .with_context(allowed_company_ids=[self.recipient.id])
            ._import_xml(content)
        )
        self.assertEqual(
            incoming.xml_validation_state, "validated", incoming.diagnostics
        )
        if reviewed:
            incoming.action_mark_reviewed()
        return incoming.with_env(self.op_env)

    def document(self, source):
        return IncomingAccountEvidenceService(self.op_env).read(source)

    def mapping(self, **kwargs):
        values = dict(
            name="Explicit supplier default",
            company_id=self.recipient.id,
            partner_id=self.supplier.id,
            match_kind="default",
            account_id=self.expense.id,
            tax_id=self.purchase_vat.id,
        )
        values.update(kwargs)
        return self.manager_env[MAPPING].create(values)

    def assert_code(self, code, callable_, *args, **kwargs):
        with self.assertRaises(IncomingAccountError) as result:
            callable_(*args, **kwargs)
        self.assertEqual(result.exception.diagnostic["code"], code)
