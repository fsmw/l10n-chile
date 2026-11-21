# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from lxml import etree

from odoo import Command

from odoo.addons.l10n_cl_dte_inbox.services.incoming_validation import xml_bytes
from odoo.addons.l10n_cl_dte_inbox_account.tests.common import IncomingAccountCase
from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.services.ted_composer import DteTedComposer
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import DteXmlComposer


class ReceivedPurchaseCase(IncomingAccountCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env(su=True)
        company = cls.recipient
        cls.revenue = cls.recipient_data["default_account_revenue"]
        cls.sale_journal = env["account.journal"].create(
            dict(
                name="Received purchase invoices",
                code="F46",
                type="sale",
                company_id=company.id,
                l10n_latam_use_documents=True,
                default_account_id=cls.revenue.id,
            )
        )
        cls.output_vat_account = cls._account(env, "219901", "Synthetic output VAT")
        cls.retained_account = cls._account(
            env, "119901", "Synthetic VAT retained by the buyer", "asset_current"
        )
        group = cls.recipient_data["default_tax_sale"].tax_group_id
        cls.sale_vat = cls._tax(
            env, "Synthetic sales VAT", 19, 14, cls.output_vat_account, group
        )
        cls.retention = cls._tax(
            env, "Synthetic VAT retained by buyer", -19, 15, cls.retained_account, group
        )
        cls.cattle_retention = cls._tax(
            env, "Synthetic cattle VAT retained 8%", -8, 32, cls.retained_account, group
        )
        cls.received46_config = dict(
            dte_inbox_account_received46_journal_id=cls.sale_journal.id,
            dte_inbox_account_received46_vat_tax_id=cls.sale_vat.id,
            dte_inbox_account_received46_retention_tax_id=cls.retention.id,
        )
        company.write(cls.received46_config)

    @classmethod
    def _account(cls, env, code, name, account_type="liability_current"):
        return env["account.account"].create(
            dict(
                code=code,
                name=name,
                account_type=account_type,
                company_ids=[Command.set(cls.recipient.ids)],
            )
        )

    @classmethod
    def _tax(cls, env, name, amount, sii_code, account, group):
        def repartition():
            return [
                Command.create(dict(repartition_type="base")),
                Command.create(dict(repartition_type="tax", account_id=account.id)),
            ]

        return env["account.tax"].create(
            dict(
                name=name,
                amount=amount,
                amount_type="percent",
                type_tax_use="sale",
                l10n_cl_sii_code=sii_code,
                company_id=cls.recipient.id,
                tax_group_id=group.id,
                price_include_override="tax_excluded",
                invoice_repartition_line_ids=repartition(),
                refund_repartition_line_ids=repartition(),
            )
        )

    def signed_purchase(
        self, retention_code="15", receiver=None, retained=None, rate="19"
    ):
        """Sign a DTE 46 the synthetic buyer issues to the recipient company."""
        invoice = self.make_invoice(
            code="33", post=False, taxable=True, partner=receiver
        )
        invoice.invoice_line_ids.write(
            {"quantity": 1, "price_unit": 1000, "discount": 0}
        )
        invoice.action_post()
        self.counter += 10
        snapshot, _caf = self.capture(invoice, code="33", folio=self.counter)
        caf = self.validate_authorization(
            document_code="46", folio_start=self.counter, folio_end=self.counter + 3
        )
        totals = dict(snapshot["totals"])
        vat_amount = int(totals["vat"])
        amount = (
            retained
            if retained is not None
            else str(round(vat_amount * int(rate) / 19))
        )
        totals["additional_taxes"] = [
            dict(code=retention_code, rate=rate, amount=amount)
        ]
        if vat_amount > int(amount):
            totals["vat_not_retained"] = str(vat_amount - int(amount))
        totals["total"] = str(int(totals["total"]) - int(amount))
        purchase = dict(
            snapshot,
            totals=totals,
            lines=[
                dict(line, additional_tax_code=retention_code)
                for line in snapshot["lines"]
            ],
        )
        ted = DteTedComposer().compose_ted(dict(purchase, document_code="46"), caf)
        root = etree.fromstring(
            DteXmlComposer().compose_unsigned_document(dict(purchase, ted_xml=ted))
        )
        for node in root.iter():
            if etree.QName(node).localname == "TipoDTE":
                node.text = "46"
            if etree.QName(node).localname == "Documento":
                node.set("ID", node.get("ID").replace("T33", "T46"))
        return SignatureService().sign_document(
            self.company.with_user(self.dte_manager), "demo", xml_bytes(root)
        )

    def purchase_source(self, **kwargs):
        return self.source(content=self.signed_purchase(**kwargs))

    def purchase_wizard(self, source=None):
        source = source if source is not None else self.purchase_source()
        action = source.action_prepare_customer_invoice()
        return self.op_env["l10n_cl.incoming.dte.bill.wizard"].browse(action["res_id"])

    def customer_invoice(self, wizard):
        self.assertFalse(wizard.has_blockers, wizard.diagnostic_text)
        action = wizard.action_confirm()
        return self.op_env["account.move"].browse(action["res_id"])
