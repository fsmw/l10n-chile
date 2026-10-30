# Copyright 2026 Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from types import SimpleNamespace
from unittest.mock import Mock, patch
from xml.sax.saxutils import escape

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from lxml import etree

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.account.tests.common import (
    AccountTestInvoicingCommon,
    AccountTestInvoicingHttpCommon,
)


class _FakeRecordSet(list):
    def filtered(self, predicate):
        return self.__class__(record for record in self if predicate(record))

    def mapped(self, field_name):
        return [getattr(record, field_name) for record in self]


@tagged("post_install", "post_install_l10n", "-at_install")
class TestPosDteBridge(AccountTestInvoicingCommon):
    @classmethod
    @AccountTestInvoicingCommon.setup_country("cl")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.order_model = cls.env["pos.order"]
        cls.currency = cls.company.currency_id
        cls.partner_a.country_id = cls.company.country_id
        cls.partner_a.vat = "60803000-K"
        cls.partner_a.l10n_cl_sii_taxpayer_type = "1"
        cls.env.user.groups_id |= cls.env.ref("l10n_cl_dte_sii.group_dte_manager")

    def _new_order(self, invoice_type="boleta", amount_tax=0.0):
        return self.order_model.new(
            {
                "company_id": self.company.id,
                "invoice_type": invoice_type,
                "amount_tax": amount_tax,
                "currency_id": self.currency.id,
            }
        )

    def test_document_code_mapping_for_invoice_and_exempt_invoice(self):
        self.assertEqual(
            self._new_order("factura", 190)._l10n_cl_pos_document_code("out_invoice"),
            "33",
        )
        self.assertEqual(
            self._new_order("factura", 0)._l10n_cl_pos_document_code("out_invoice"),
            "34",
        )
        self.assertEqual(
            self._new_order("boleta", 190)._l10n_cl_pos_document_code("out_invoice"),
            "39",
        )
        self.assertEqual(
            self._new_order("boleta", 0)._l10n_cl_pos_document_code("out_invoice"), "41"
        )

    def test_invalid_document_kind_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._new_order("other")._l10n_cl_pos_document_code("out_invoice")

    def test_pos_reference_annuls_only_when_this_credit_note_covers_full_amount(self):
        source_move = Mock(currency_id=self.currency, amount_total=2000.0)
        credit_note = Mock(amount_total=1000.0)
        order = self._new_order()

        self.assertEqual(
            order._l10n_cl_pos_credit_note_reference_code(
                credit_note, source_move, full_quantity_return=False
            ),
            "3",
        )
        credit_note.amount_total = 2000.0
        self.assertEqual(
            order._l10n_cl_pos_credit_note_reference_code(
                credit_note, source_move, full_quantity_return=False
            ),
            "3",
        )
        self.assertEqual(
            order._l10n_cl_pos_credit_note_reference_code(
                credit_note, source_move, full_quantity_return=True
            ),
            "1",
        )
        source_move.amount_total = 0.0
        credit_note.amount_total = 0.0
        self.assertEqual(
            order._l10n_cl_pos_credit_note_reference_code(
                credit_note, source_move, full_quantity_return=False
            ),
            "3",
        )

    def test_pos_full_return_quantity_uses_only_current_refund_order(self):
        refund_order = self._new_order()
        uom = SimpleNamespace(rounding=1)
        first_refund_line = SimpleNamespace(qty=-2, order_id=refund_order)
        second_refund_line = SimpleNamespace(qty=-1, order_id=refund_order)
        first_source_line = SimpleNamespace(
            qty=2,
            product_uom_id=uom,
            refund_orderline_ids=_FakeRecordSet([first_refund_line]),
        )
        second_source_line = SimpleNamespace(
            qty=1,
            product_uom_id=uom,
            refund_orderline_ids=_FakeRecordSet([second_refund_line]),
        )
        source_order = SimpleNamespace(
            lines=_FakeRecordSet([first_source_line, second_source_line])
        )

        self.assertTrue(
            refund_order._l10n_cl_pos_refund_covers_all_source_quantities(source_order)
        )
        second_refund_line.qty = 0
        self.assertFalse(
            refund_order._l10n_cl_pos_refund_covers_all_source_quantities(source_order)
        )

    def test_card_voucher_flag_is_explicit_for_bank_payment_methods(self):
        bank_method = self.env["pos.payment.method"].create(
            {
                "name": "POS DTE Bank Transfer Test",
                "journal_id": self.company_data["default_journal_bank"].id,
                "receivable_account_id": self.company_data[
                    "default_account_receivable"
                ].id,
                "company_id": self.company.id,
            }
        )
        self.assertEqual(bank_method.journal_id.type, "bank")
        self.assertFalse(bank_method.is_card_payment)
        bank_method.is_card_payment = True
        self.assertTrue(bank_method.is_card_payment)

    def test_uncertain_submission_can_be_reconciled_from_pos_order(self):
        document_type = self.env["l10n_latam.document.type"].search(
            [("country_id.code", "=", "CL"), ("code", "=", "33")], limit=1
        )
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": self.company_data["default_journal_sale"].id,
                "partner_id": self.partner_a.id,
                "invoice_date": "2026-10-30",
                "l10n_latam_document_type_id": document_type.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Servicio POS",
                            "quantity": 1,
                            "price_unit": 1000,
                            "account_id": self.company_data[
                                "default_account_revenue"
                            ].id,
                        },
                    )
                ],
            }
        )
        move.action_post()
        move._l10n_cl_write_dte_lifecycle_values({"l10n_cl_dte_status": "error"})
        order = self._new_order("factura")
        order.account_move = move

        action = order.action_l10n_cl_dte_reconcile_uncertain()

        self.assertEqual(action["res_model"], "l10n_cl.dte.reconciliation.wizard")
        self.assertEqual(action["context"]["default_move_id"], move.id)

    def test_server_invoice_partner_validation_rejects_missing_fiscal_data(self):
        partner = self.env["res.partner"].create(
            {
                "name": "Cliente POS incompleto",
                "country_id": self.company.country_id.id,
                "vat": "76543210-3",
            }
        )

        with self.assertRaisesRegex(ValidationError, "Complete los datos requeridos"):
            self.order_model._l10n_cl_validate_pos_invoice_partner(partner)

    def test_server_invoice_partner_validation_accepts_complete_partner(self):
        identification_type = self.env["l10n_latam.identification.type"].search(
            [("country_id.code", "=", "CL")], limit=1
        )
        partner = self.env["res.partner"].create(
            {
                "name": "Cliente POS completo",
                "country_id": self.company.country_id.id,
                "vat": "76543210-3",
                "street": "Dirección de prueba 123",
                "l10n_cl_dte_email": "cliente@example.test",
                "l10n_cl_activity_description": "Servicios de prueba",
                "l10n_latam_identification_type_id": identification_type.id,
                "l10n_cl_sii_taxpayer_type": "1",
            }
        )

        self.order_model._l10n_cl_validate_pos_invoice_partner(partner)

    def test_dte_identity_cannot_change_after_invoice_is_linked(self):
        for field_name, value in (
            ("invoice_type", "boleta"),
            ("voucher_number", "VOUCHER-2"),
        ):
            order = self._new_order("factura")
            order.account_move = self.env["account.move"].create(
                {
                    "move_type": "out_invoice",
                    "company_id": self.company.id,
                    "journal_id": self.company_data["default_journal_sale"].id,
                    "partner_id": self.company.partner_id.id,
                }
            )
            with self.subTest(field=field_name), self.assertRaises(UserError):
                order.write({field_name: value})

    def test_credit_note_requires_a_source_pos_dte(self):
        with self.assertRaises(UserError):
            self._new_order()._l10n_cl_pos_document_code("out_refund")

    def test_ted_is_rendered_as_pdf417_png(self):
        ted = etree.fromstring(
            b"<TED version='1.0'><DD><RE>76543210-3</RE><TD>39</TD>"
            b"<F>12</F><FE>2026-10-29</FE><RR>66666666-6</RR>"
            b"<MNT>1190</MNT><IT1>Producto</IT1></DD></TED>"
        )
        encoded = self.env["account.move"]._l10n_cl_dte_pos_render_ted(ted)
        self.assertTrue(base64.b64decode(encoded).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_pos_user_does_not_inherit_accounting_dte_role(self):
        pos_user = self.env.ref("point_of_sale.group_pos_user")
        self.assertNotIn(
            self.env.ref("l10n_cl_dte_sii.group_dte_user"), pos_user.implied_ids
        )

    def test_unrelated_company_pos_configuration_is_not_blocked(self):
        non_chilean = self.env["res.company"].create(
            {"name": "POS non Chile", "currency_id": self.currency.id}
        )
        config = self.env["pos.config"].new({"company_id": non_chilean.id})
        self.assertTrue(config._l10n_cl_validate_dte_setup())


@tagged("post_install", "-at_install")
class TestPosDteFrontend(AccountTestInvoicingHttpCommon):
    def authenticate(self, login, password, browser=None):
        # Newer Chrome waits for the Network domain before acknowledging
        # Network.setCookie through the DevTools protocol.
        if browser:
            browser._websocket_request("Network.enable")
        return super().authenticate(login, password, browser=browser)

    def test_sync_from_ui_rejects_incomplete_invoice_customer(self):
        self.pos_config.with_user(self.pos_user).open_ui()
        session = self.env["pos.session"].search(
            [
                ("config_id", "=", self.pos_config.id),
                ("state", "in", ["opening_control", "opened"]),
            ],
            limit=1,
        )
        partner = self.env["res.partner"].create(
            {
                "name": "Cliente POS incompleto por sync",
                "country_id": self.company.country_id.id,
                "vat": "76543210-3",
            }
        )
        self.assertTrue(session)

        with self.assertRaisesRegex(ValidationError, "Complete los datos requeridos"):
            self.env["pos.order"]._process_order(
                {
                    "session_id": session.id,
                    "partner_id": partner.id,
                    "invoice_type": "factura",
                },
                False,
            )

    def test_chilean_pos_requires_explicit_invoice_journal(self):
        self.pos_config.write({"invoice_journal_id": False})
        with self.assertRaisesRegex(UserError, "Configure un diario de facturas"):
            self.pos_config.with_user(self.pos_user).open_ui()

    @classmethod
    @AccountTestInvoicingHttpCommon.setup_country("cl")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.sale_journal = cls.company_data["default_journal_sale"]
        cls.env.user.groups_id |= cls.env.ref("l10n_cl_dte_sii.group_dte_manager")
        cls.company.write(
            {
                "vat": "76543210-3",
                "l10n_cl_dte_environment": "SIIDEMO",
                "l10n_cl_dte_activity": "Comercio de prueba",
                "l10n_cl_dte_activity_code": "471100",
                "l10n_cl_dte_resolution_number": "0",
                "l10n_cl_dte_resolution_date": "2020-01-01",
            }
        )
        cls.company.partner_id.write(
            {
                "street": "Origen 123",
                "city": "Santiago",
                "email": "pos-company@example.test",
            }
        )
        cls.env.user.email = "dte-admin@example.test"
        cls.env["ir.config_parameter"].sudo().set_param(
            "mail.default.from", "dte-pos@example.test"
        )
        cls.sale_journal.write(
            {
                "l10n_latam_use_documents": True,
                "l10n_cl_dte_point_of_sale": "online",
                "l10n_cl_dte_sucursal": "1",
                "l10n_cl_dte_office_number": "1",
            }
        )
        cls.caf_key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        caf_numbers = cls.caf_key.public_key().public_numbers()
        caf_modulus = base64.b64encode(caf_numbers.n.to_bytes(128, "big")).decode()
        caf_exponent = base64.b64encode(caf_numbers.e.to_bytes(3, "big")).decode()
        caf_private_key = cls.caf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        # The tour sells an exempt POS product, so its boleta is type 41.
        # It also exercises the taxable invoice (39) and its credit note (61).
        for document_code in ("39", "41", "61"):
            caf_xml = (
                "<AUTORIZACION><CAF version='1.0'><DA>"
                f"<RE>{cls.company.vat}</RE><RS>Emisor POS</RS><TD>{document_code}</TD>"
                "<RNG><D>100</D><H>110</H></RNG><FA>2020-01-01</FA>"
                f"<RSAPK><M>{caf_modulus}</M><E>{caf_exponent}</E></RSAPK>"
                "<IDK>100</IDK></DA><FRMA algoritmo='SHA1withRSA'>AA==</FRMA>"
                f"<RSASK>{escape(caf_private_key)}</RSASK>"
                "</CAF></AUTORIZACION>"
            ).encode()
            caf = cls.env["l10n_cl.dte.caf"].create_from_xml(
                f"pos-tour-caf-{document_code}.xml", caf_xml, cls.company
            )
            caf.action_enable()
        cls.certificate = cls._synthetic_certificate(cls.company.vat)
        cls.pos_user = cls.env["res.users"].create(
            {
                "name": "POS DTE cashier",
                "login": "pos_dte_cashier",
                "password": "pos_dte_cashier",
                "email": "pos-cashier@example.test",
                "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id])],
                "groups_id": [
                    (4, cls.env.ref("base.group_user").id),
                    (4, cls.env.ref("point_of_sale.group_pos_user").id),
                ],
            }
        )
        cls.cash_method = cls.env["pos.payment.method"].create(
            {
                "name": "POS DTE Cash",
                "journal_id": cls.company_data["default_journal_cash"].id,
                "receivable_account_id": cls.company_data[
                    "default_account_receivable"
                ].id,
                "company_id": cls.company.id,
            }
        )
        cls.transfer_method = cls.env["pos.payment.method"].create(
            {
                "name": "POS DTE Bank Transfer",
                "journal_id": cls.company_data["default_journal_bank"].id,
                "receivable_account_id": cls.company_data[
                    "default_account_receivable"
                ].id,
                "company_id": cls.company.id,
                "is_card_payment": False,
            }
        )
        cls.card_method = cls.env["pos.payment.method"].create(
            {
                "name": "POS DTE Card",
                "journal_id": cls.company_data["default_journal_bank"].id,
                "receivable_account_id": cls.company_data[
                    "default_account_receivable"
                ].id,
                "company_id": cls.company.id,
                "is_card_payment": True,
            }
        )
        cls.pos_config = cls.env["pos.config"].create(
            {
                "name": "POS DTE Tour",
                "company_id": cls.company.id,
                "journal_id": cls.company_data["default_journal_misc"].id,
                "invoice_journal_id": cls.sale_journal.id,
                "payment_method_ids": [
                    (4, cls.cash_method.id),
                    (4, cls.transfer_method.id),
                    (4, cls.card_method.id),
                ],
            }
        )
        identification_type = cls.env["l10n_latam.identification.type"].search(
            [("country_id.code", "=", "CL")], limit=1
        )
        cls.invoice_partner = cls.env["res.partner"].create(
            {
                "name": "POS DTE Invoice Buyer",
                "country_id": cls.company.country_id.id,
                "vat": "76123456-0",
                "street": "Avenida de Prueba 123",
                "city": "Santiago",
                "l10n_cl_dte_email": "invoice-buyer@example.test",
                "l10n_cl_activity_description": "Servicios de prueba",
                "l10n_latam_identification_type_id": identification_type.id,
                "l10n_cl_sii_taxpayer_type": "1",
            }
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "POS DTE Tour Product",
                "available_in_pos": True,
                "list_price": 1000,
                "taxes_id": [(5, 0, 0)],
            }
        )

    def test_pos_sale_and_refund_sync_as_dte(self):
        self.pos_config.with_user(self.pos_user).open_ui()
        prior_order_ids = set(
            self.env["pos.order"].search([("config_id", "=", self.pos_config.id)]).ids
        )
        with patch.object(
            type(self.company), "_get_digital_signature", return_value=self.certificate
        ):
            self.start_tour(
                f"/pos/ui?config_id={self.pos_config.id}",
                "l10n_cl_pos_sale_refund_tour",
                login="pos_dte_cashier",
            )
        new_orders = (
            self.env["pos.order"]
            .search([("config_id", "=", self.pos_config.id)])
            .filtered(lambda record: record.id not in prior_order_ids)
        )
        self.assertEqual(len(new_orders), 6)
        invoice_orders = new_orders.filtered(
            lambda record: record.account_move.move_type == "out_invoice"
        )
        self.assertEqual(len(invoice_orders), 4)
        order = invoice_orders.filtered(
            lambda record: record.invoice_type == "boleta"
            and self.cash_method in record.payment_ids.payment_method_id
        )
        self.assertEqual(len(order), 1)
        self.assertTrue(order)
        self.assertTrue(order.account_move)
        self.assertEqual(order.account_move.l10n_latam_document_type_id.code, "41")
        self.assertEqual(order.account_move.l10n_cl_dte_status, "not_sent")
        move = order.account_move
        pos_move = move.with_user(self.pos_user)
        self.assertFalse(
            self.pos_user.has_group("l10n_cl_dte_sii.group_dte_user")
        )
        pos_move.check_access("read")
        pos_move._check_dte_action_access()
        with self.assertRaises(AccessError):
            pos_move.check_access("write")

        unrelated_move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": self.sale_journal.id,
                "partner_id": self.partner_a.id,
            }
        )
        with self.assertRaises(AccessError):
            unrelated_move.with_user(self.pos_user).check_access("read")

        generated_move = order._generate_pos_order_invoice()
        self.assertEqual(generated_move["res_id"], move.id)
        replay_result = self.env["pos.order"].sync_from_ui(
            [
                {
                    "uuid": order.uuid,
                    "name": order.pos_reference,
                    "session_id": order.session_id.id,
                    "state": "paid",
                    "lines": [],
                    "invoice_type": "factura",
                    "voucher_number": "MUST-NOT-OVERWRITE-ISSUED-DTE",
                }
            ]
        )
        self.assertEqual(replay_result["pos.order"][0]["id"], order.id)
        self.assertNotIn("account.move", replay_result)
        replayed_order = replay_result["pos.order"][0]
        self.assertEqual(replayed_order["account_move"], move.id)
        self.assertEqual(replayed_order["l10n_cl_dte_folio"], move.l10n_cl_dte_folio)
        self.assertEqual(
            replayed_order["l10n_cl_dte_document_number"],
            move.l10n_latam_document_number,
        )
        self.assertEqual(order.invoice_type, "boleta")
        factura_order = invoice_orders.filtered(
            lambda record: record.invoice_type == "factura"
        )
        self.assertEqual(len(factura_order), 1)
        self.assertEqual(factura_order.partner_id, self.invoice_partner)
        self.assertEqual(
            factura_order.account_move.l10n_latam_document_type_id.code, "33"
        )
        self.assertTrue(factura_order.account_move.l10n_cl_dte_folio)
        self.assertEqual(factura_order.account_move.l10n_cl_dte_status, "not_sent")
        self.assertEqual(
            self.env["account.move"].search_count(
                [
                    ("company_id", "=", self.company.id),
                    ("move_type", "=", "out_invoice"),
                    ("ref", "=", order.name),
                ]
            ),
            1,
        )
        credit_orders = new_orders.filtered(
            lambda record: record.account_move.move_type == "out_refund"
        )
        self.assertEqual(len(credit_orders), 2)
        references = []
        for credit_order in credit_orders.sorted("id"):
            credit_note = credit_order.account_move
            self.assertEqual(credit_note.l10n_latam_document_type_id.code, "61")
            self.assertEqual(len(credit_note.l10n_cl_reference_ids), 1)
            reference = credit_note.l10n_cl_reference_ids
            self.assertEqual(
                reference.origin_doc_number,
                order.account_move.l10n_latam_document_number,
            )
            references.append(reference.reference_doc_code)
        # The second credit note completes the cumulative return quantities,
        # but it still credits only half of the original amount.  It corrects
        # the amount; it does not annul the original DTE by itself.
        self.assertEqual(references, ["3", "3"])
        transfer_order = invoice_orders.filtered(
            lambda record: self.transfer_method
            in record.payment_ids.payment_method_id
        )
        card_order = invoice_orders.filtered(
            lambda record: self.card_method in record.payment_ids.payment_method_id
        )
        self.assertFalse(transfer_order.voucher_number)
        self.assertEqual(card_order.voucher_number, "CARD-TOUR-123")

    @staticmethod
    def _synthetic_certificate(rut):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = key.public_key().public_numbers()

        class SyntheticCertificate:
            subject_serial_number = rut

            def _sign(self, data, hashing_algorithm, formatting):
                return base64.b64encode(
                    key.sign(data, padding.PKCS1v15(), hashes.SHA1())
                )

            def _get_public_key_numbers_bytes(self, formatting):
                return (
                    base64.b64encode(
                        numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
                    ),
                    base64.b64encode(
                        numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
                    ),
                )

            def _get_der_certificate_bytes(self, formatting):
                return base64.b64encode(b"synthetic-certificate")

        return SyntheticCertificate()
