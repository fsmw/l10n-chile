# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import json
from decimal import Decimal
from unittest.mock import patch

from lxml import etree

from odoo import Command
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_account.services.dte_issuance_service import (
    DteIssuanceService,
)
from odoo.addons.l10n_cl_dte_boleta.services.boleta_snapshot import (
    BoletaSnapshotService,
)
from odoo.addons.l10n_cl_dte_pos.services.pos_boleta_issuance_adapter import (
    PosBoletaIssuanceAdapter,
)
from odoo.addons.l10n_cl_dte_signing.tests.common import SigningTestCase
from odoo.addons.point_of_sale.tests.common import TestPoSCommon


@tagged("post_install", "-at_install")
class TestMixedPosBoletaTotals(TestPoSCommon, SigningTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.company.sudo().write(
            {
                "country_id": cls.env.ref("base.cl").id,
                "currency_id": cls.env.ref("base.CLP").id,
                "dte_sii_environment": "demo",
                "dte_sii_activity_code": "620200",
                "l10n_cl_activity_description": "Synthetic POS services",
            }
        )
        cls.company.partner_id.sudo().write(
            {
                "name": "Synthetic POS Issuer",
                "vat": "76864280-K",
                "street": "Test 1",
                "city": "Santiago",
            }
        )
        cls.tax_19 = cls.taxes["tax7"]
        cls.tax_19.write({"amount": 19, "name": "POS mixed test VAT 19%"})
        cls.taxable_product = cls.create_product(
            "Taxable DTE Product",
            cls.categ_basic,
            100,
            tax_ids=[cls.tax_19.id],
        )
        cls.exempt_product = cls.create_product(
            "Exempt DTE Product",
            cls.categ_basic,
            500,
            tax_ids=[],
        )
        cls.config = cls.basic_config
        cls.config.pricelist_id.currency_id = cls.env.ref("base.CLP")
        cls.config.pricelist_id.item_ids.unlink()
        cls.config.write(
            {
                "cash_control": True,
                "payment_method_ids": [Command.link(cls.bank_pm1.id)],
                "available_pricelist_ids": [Command.set(cls.config.pricelist_id.ids)],
            }
        )
        cls.config.payment_method_ids.write({"l10n_cl_dte_boleta_payment_method": "2"})
        cls.customer.write(
            {
                "name": "Synthetic POS Receiver",
                "vat": "76864280-K",
                "country_id": cls.env.ref("base.cl").id,
            }
        )
        for user in (cls.dte_manager, cls.dte_user):
            user.sudo().write(
                {
                    "company_id": cls.company.id,
                    "company_ids": [Command.link(cls.company.id)],
                }
            )
        cls.dte_manager.sudo().write(
            {
                "groups_id": [
                    Command.link(cls.env.ref("l10n_cl_dte_core.group_dte_manager").id),
                    Command.link(cls.env.ref("point_of_sale.group_pos_user").id),
                ]
            }
        )
        cls.env = (
            cls.env["res.users"]
            .with_user(cls.dte_manager)
            .with_company(cls.company)
            .env
        )

    def test_mixed_summary_boleta_reconciles_snapshot_and_signed_xml(self):
        self.company.sudo().dte_pos_accounting_mode = "session_summary"
        authorization = self.validate_authorization(
            document_code="39", folio_start=820, folio_end=825
        )
        self.create_signing_profile()
        self.open_new_session()
        order_data = self.create_ui_order_data(
            [(self.taxable_product, 1), (self.exempt_product, 1)],
            customer=self.customer,
        )
        with patch.object(DteIssuanceService, "issue", autospec=True):
            response = self.env["pos.order"].sudo().sync_from_ui([order_data])
        order = (
            self.env["pos.order"]
            .sudo()
            .browse(response["pos.order"][0]["id"])
            .with_user(self.dte_manager)
            .with_company(self.company)
        )
        self.assertFalse(order.account_move)
        self.assertFalse(order.env.su)
        self.assertTrue(order.has_access("read") and order.has_access("write"))
        self.assertEqual(order.amount_total, 619)
        original_totals = (order.amount_total, order.amount_tax)

        adapter = PosBoletaIssuanceAdapter()
        with patch.object(
            BoletaSnapshotService,
            "_annual_135_uf_limit_for",
            return_value=Decimal("999999999"),
        ):
            document = DteIssuanceService().prepare(
                order.with_context(dte_caf_authorization_id=authorization.id),
                adapter,
                "39",
            )

        snapshot_artifact = document.artifact_ids.filtered(
            lambda artifact: artifact.role == "source_snapshot"
        )
        snapshot = json.loads(base64.b64decode(snapshot_artifact.content))
        self.assertEqual(
            snapshot["totals"],
            {
                "net": "100",
                "exempt": "500",
                "vat_rate": "19",
                "vat": "19",
                "total": "619",
            },
        )

        signed_artifact = document.artifact_ids.filtered(
            lambda artifact: artifact.role == "signed_xml"
        )
        signed_xml = base64.b64decode(signed_artifact.content, validate=True)
        adapter.validate_document(signed_xml)
        root = etree.fromstring(signed_xml)
        namespace = {"sii": "http://www.sii.cl/SiiDte"}
        self.assertEqual(root.findtext(".//sii:MntNeto", namespaces=namespace), "100")
        self.assertEqual(root.findtext(".//sii:MntExe", namespaces=namespace), "500")
        self.assertEqual(root.findtext(".//sii:IVA", namespaces=namespace), "19")
        self.assertEqual(root.findtext(".//sii:MntTotal", namespaces=namespace), "619")
        self.assertEqual((order.amount_total, order.amount_tax), original_totals)
        self.assertEqual(document.preparation_state, "signed")
        self.assertFalse(
            self.env["l10n_cl.electronic.submission"].search(
                [("document_id", "=", document.id)]
            ),
            "prepare() must not enqueue or send the DTE",
        )
