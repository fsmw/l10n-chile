# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import base64
import hashlib
import unittest
from datetime import date
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from lxml import etree

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


class TestRcvNormalization(TransactionCase):
    def test_real_field_names_and_amounts_normalize(self):
        normalizer = self.env["l10n.cl.rcv.normalizer"]
        self.assertEqual(normalizer._vat("76.123.456-7"), "76123456-7")
        self.assertEqual(normalizer._date("23/08/2026").isoformat(), "2026-08-23")
        self.assertEqual(normalizer._integer(None), 0)

    def _make_run(self):
        company = self.env.company
        period = self.env["l10n.cl.rcv.period"].create(
            {
                "company_id": company.id,
                "year": 2026,
                "month": 8,
                "operation": "purchase",
            }
        )
        run = self.env["l10n.cl.rcv.sync"].create(
            {
                "period_id": period.id,
                "company_id": company.id,
                "operation": "purchase",
                "requested_by": self.env.user.id,
            }
        )
        return period, run

    def test_missing_detail_keeps_summary_and_marks_partial(self):
        period, run = self._make_run()
        result = self.env["l10n.cl.rcv.normalizer"]._apply(
            run,
            [
                {
                    "state": "PENDIENTE",
                    "unavailable_details": ["33"],
                    "summary": {
                        "data": [
                            {
                                "rsmnTipoDocInteger": 33,
                                "rsmnTotDoc": 2,
                                "rsmnMntNeto": 200,
                                "rsmnMntExe": 0,
                                "rsmnMntIVA": 38,
                                "rsmnMntTotal": 238,
                            }
                        ]
                    },
                    "details": {"33": []},
                }
            ],
        )
        self.assertEqual(result, "partial")
        self.assertEqual(run.summary_ids.document_count, 2)
        self.assertEqual(run.detail_count, 0)
        self.assertEqual(period.year, 2026)

    def test_boleta_period_stays_pending_without_fabricated_rows(self):
        period = self.env["l10n.cl.rcv.period"].create(
            {
                "company_id": self.env.company.id,
                "year": 2026,
                "month": 8,
                "operation": "boleta",
            }
        )
        period.action_sync()
        self.assertEqual(period.state, "pending")
        self.assertEqual(period.current_sync_id.state, "pending")
        self.assertIn("pendiente de validar", period.current_sync_id.error_message)
        self.assertFalse(period.current_sync_id.document_ids)

    def test_complete_detail_is_idempotent_across_runs(self):
        period, run = self._make_run()
        payload = [
            {
                "state": "REGISTRO",
                "summary": {
                    "data": [
                        {
                            "rsmnTipoDocInteger": 33,
                            "rsmnTotDoc": 1,
                            "rsmnMntNeto": 100,
                            "rsmnMntExe": 0,
                            "rsmnMntIVA": 19,
                            "rsmnMntTotal": 119,
                        }
                    ]
                },
                "details": {
                    "33": [
                        {
                            "detTipoDoc": 33,
                            "detNroDoc": 934,
                            "detFchDoc": "2026-08-15",
                            "detRutDoc": 76123456,
                            "detDvDoc": "7",
                            "detRznSoc": "Proveedor prueba",
                            "detMntNeto": 100,
                            "detMntExe": 0,
                            "detMntIVA": 19,
                            "detMntTotal": 119,
                        }
                    ]
                },
            }
        ]
        normalizer = self.env["l10n.cl.rcv.normalizer"]
        self.assertEqual(normalizer._apply(run, payload), "success")
        self.assertEqual(run.detail_count, 1)
        document = run.document_ids
        self.assertEqual(document.folio, "934")
        self.assertEqual(document.issuer_vat, "76123456-7")

        next_run = self.env["l10n.cl.rcv.sync"].create(
            {
                "period_id": period.id,
                "company_id": period.company_id.id,
                "operation": "purchase",
                "requested_by": self.env.user.id,
            }
        )
        self.assertEqual(normalizer._apply(next_run, payload), "success")
        self.assertEqual(
            self.env["l10n.cl.rcv.document"].search_count(
                [
                    ("company_id", "=", period.company_id.id),
                    ("operation", "=", "purchase"),
                    ("document_type_code", "=", "33"),
                    ("issuer_vat", "=", "76123456-7"),
                    ("folio", "=", "934"),
                ]
            ),
            1,
        )
        self.assertEqual(document.observation_ids.__len__(), 2)

    def test_matcher_finds_move_by_partner_type_and_folio(self):
        company = self.env.company
        partner = self.env["res.partner"].create(
            {"name": "Proveedor RCV", "vat": "CL 76.123.456-0"}
        )
        document_type = self.env["l10n_latam.document.type"].search(
            [("code", "=", "33"), ("country_id.code", "=", "CL")], limit=1
        )
        self.assertTrue(document_type)
        journal = self.env["account.journal"].create(
            {
                "name": "RCV purchases",
                "code": "RCVT",
                "type": "purchase",
                "company_id": company.id,
            }
        )
        move = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "company_id": company.id,
                "partner_id": partner.id,
                "journal_id": journal.id,
                "invoice_date": "2026-08-12",
                "date": "2026-08-31",
                "l10n_latam_document_type_id": document_type.id,
                "l10n_latam_document_number": "934",
            }
        )
        doc = self.env["l10n.cl.rcv.document"].create(
            {
                "company_id": company.id,
                "operation": "purchase",
                "tax_period": "202608",
                "document_type_code": "33",
                "issuer_vat": "76123456-0",
                "folio": "934",
                "reconciliation_state": "pending",
                "tax_breakdown_available": True,
            }
        )
        matcher = self.env["l10n.cl.rcv.matcher"]
        partners = self.env["res.partner"].search(
            [("vat", "in", list(matcher._vat_variants("76123456-0")))]
        )
        self.assertIn(partner, partners, partner.vat)
        candidates = self.env["l10n.cl.rcv.matcher"]._candidate_moves(doc)
        self.assertEqual(candidates, move)
        self.env["l10n.cl.rcv.matcher"]._reconcile(doc)
        self.assertEqual(doc.move_id, move)
        self.assertEqual(doc.reconciliation_state, "matched")
        doc.tax_period = "202609"
        self.env["l10n.cl.rcv.matcher"]._reconcile(doc)
        self.assertEqual(doc.reconciliation_state, "matched")
        self.assertEqual(move.invoice_date.isoformat(), "2026-08-12")
        self.assertEqual(move.date.isoformat(), "2026-09-30")
        doc.total_amount = 1
        self.env["l10n.cl.rcv.matcher"]._reconcile(doc)
        self.assertEqual(doc.reconciliation_state, "review")
        self.assertIn("IVA fiscal cero", doc.automation_error)

    def test_matcher_keeps_draft_in_review_when_rcv_period_is_locked(self):
        company = self.env.company
        partner = self.env["res.partner"].create(
            {"name": "Proveedor RCV", "vat": "76123456-0"}
        )
        document_type = self.env["l10n_latam.document.type"].search(
            [("code", "=", "33"), ("country_id.code", "=", "CL")], limit=1
        )
        journal = self.env["account.journal"].create(
            {
                "name": "RCV locked purchases",
                "code": "RCVL",
                "type": "purchase",
                "company_id": company.id,
            }
        )
        move = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "company_id": company.id,
                "partner_id": partner.id,
                "journal_id": journal.id,
                "invoice_date": "2026-08-12",
                "date": "2026-08-31",
                "l10n_latam_document_type_id": document_type.id,
                "l10n_latam_document_number": "935",
            }
        )
        document = self.env["l10n.cl.rcv.document"].create(
            {
                "company_id": company.id,
                "operation": "purchase",
                "tax_period": "202609",
                "document_type_code": "33",
                "issuer_vat": "76123456-0",
                "folio": "935",
            }
        )
        lock_dates = [(date(2026, 8, 31), "tax_lock_date")]
        with patch.object(
            type(move), "_get_violated_lock_dates", return_value=lock_dates
        ):
            self.env["l10n.cl.rcv.matcher"]._reconcile(document)

        self.assertEqual(document.reconciliation_state, "review")
        self.assertIn("bloqueos contables", document.automation_error)
        self.assertEqual(move.date.isoformat(), "2026-08-31")
        self.assertEqual(move.invoice_date.isoformat(), "2026-08-12")

    def test_matcher_does_not_change_posted_move_outside_rcv_period(self):
        company = self.env.company
        partner = self.env["res.partner"].create(
            {"name": "Proveedor RCV", "vat": "76123456-0"}
        )
        document_type = self.env["l10n_latam.document.type"].search(
            [("code", "=", "33"), ("country_id.code", "=", "CL")], limit=1
        )
        journal = self.env["account.journal"].create(
            {
                "name": "RCV posted purchases",
                "code": "RCVP",
                "type": "purchase",
                "company_id": company.id,
            }
        )
        expense = self.env["account.account"].create(
            {
                "name": "RCV posted expense",
                "code": "RCVPEXP",
                "account_type": "expense",
                "company_ids": [(6, 0, company.ids)],
            }
        )
        payable = self.env["account.account"].create(
            {
                "name": "RCV posted payable",
                "code": "RCVPAY2",
                "account_type": "liability_payable",
                "reconcile": True,
                "company_ids": [(6, 0, company.ids)],
            }
        )
        partner.property_account_payable_id = payable
        move = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "company_id": company.id,
                "partner_id": partner.id,
                "journal_id": journal.id,
                "invoice_date": "2026-08-12",
                "date": "2026-08-31",
                "l10n_latam_document_type_id": document_type.id,
                "l10n_latam_document_number": "936",
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Compra publicada",
                            "quantity": 1,
                            "price_unit": 100,
                            "account_id": expense.id,
                        },
                    )
                ],
            }
        )
        move.action_post()
        posted_snapshot = (
            move.state,
            move.date,
            move.invoice_date,
            move.invoice_line_ids.ids,
            move.line_ids.mapped("balance"),
        )
        document = self.env["l10n.cl.rcv.document"].create(
            {
                "company_id": company.id,
                "operation": "purchase",
                "tax_period": "202609",
                "document_type_code": "33",
                "issuer_vat": "76123456-0",
                "folio": "936",
                "net_amount": 100,
                "total_amount": 100,
            }
        )

        self.env["l10n.cl.rcv.matcher"]._reconcile(document)

        self.assertEqual(document.reconciliation_state, "review")
        self.assertIn("ya está publicado", document.automation_error)
        self.assertEqual(
            (
                move.state,
                move.date,
                move.invoice_date,
                move.invoice_line_ids.ids,
                move.line_ids.mapped("balance"),
            ),
            posted_snapshot,
        )

    def test_normalized_rut_variants_include_country_prefix_and_spaces(self):
        matcher = self.env["l10n.cl.rcv.matcher"]
        self.assertEqual(matcher._normalize_vat(" CL 76.123.456-0 "), "76123456-0")
        self.assertEqual(matcher._normalize_vat("CL761234560"), "76123456-0")
        self.assertIn("CL 76.123.456-0", matcher._vat_variants("76123456-0"))

    def test_section_apply_rolls_back_failed_section_only(self):
        period, run = self._make_run()
        result = self.env["l10n.cl.rcv.normalizer"]._apply(
            run,
            [
                {
                    "state": "REGISTRO",
                    "summary": {"data": [{"rsmnTipoDocInteger": 33, "rsmnTotDoc": 0}]},
                    "details": {"33": []},
                },
                {
                    "state": "PENDIENTE",
                    "summary": {"data": [{"rsmnTipoDocInteger": 33, "rsmnTotDoc": 1}]},
                    "details": {"33": 1},
                },
            ],
        )
        self.assertEqual(result, "partial")
        self.assertEqual(run.summary_ids.mapped("sii_state"), ["REGISTRO"])
        self.assertEqual(
            run.section_ids.filtered(lambda row: row.sii_state == "REGISTRO").state,
            "complete",
        )
        self.assertEqual(
            run.section_ids.filtered(lambda row: row.sii_state == "PENDIENTE").state,
            "failed",
        )

    def test_move_absence_requires_all_purchase_states_complete(self):
        company = self.env.company
        period, run = self._make_run()
        doc_type = self.env["l10n_latam.document.type"].search(
            [("code", "=", "33"), ("country_id.code", "=", "CL")], limit=1
        )
        partner = self.env["res.partner"].create(
            {"name": "Proveedor RCV", "vat": "CL 76.123.456-0"}
        )
        journal = self.env["account.journal"].create(
            {
                "name": "RCV inverse",
                "code": "RCVI",
                "type": "purchase",
                "company_id": company.id,
            }
        )
        move = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "company_id": company.id,
                "partner_id": partner.id,
                "journal_id": journal.id,
                "invoice_date": "2026-08-12",
                "l10n_latam_document_type_id": doc_type.id,
                "l10n_latam_document_number": "999",
            }
        )
        coverage_model = self.env["l10n.cl.rcv.section"]
        for state in ("REGISTRO", "RECLAMADO", "PENDIENTE"):
            coverage_model.create(
                {"sync_id": run.id, "sii_state": state, "state": "complete"}
            )
        self.env["l10n.cl.rcv.matcher"]._reconcile_moves(run)
        self.assertEqual(move.l10n_cl_rcv_reconciliation_state, "not_in_sii")
        pending_section = coverage_model.search(
            [("sync_id", "=", run.id), ("sii_state", "=", "PENDIENTE")]
        )
        pending_section.write({"state": "partial"})
        self.assertEqual(pending_section.state, "partial")
        self.env["l10n.cl.rcv.matcher"]._reconcile_moves(run)
        self.assertEqual(move.l10n_cl_rcv_reconciliation_state, "indeterminate")

    def test_draft_requires_lines_after_confirmation_prompt(self):
        partner = self.env["res.partner"].create(
            {"name": "Proveedor RCV", "vat": "76123456-7"}
        )
        doc = self.env["l10n.cl.rcv.document"].create(
            {
                "company_id": self.env.company.id,
                "operation": "purchase",
                "tax_period": "202608",
                "document_type_code": "33",
                "issuer_vat": "76123456-7",
                "folio": "935",
                "reconciliation_state": "missing",
            }
        )
        wizard = self.env["l10n.cl.rcv.draft.wizard"].create(
            {
                "document_id": doc.id,
                "partner_id": partner.id,
            }
        )
        with self.assertRaises(UserError):
            wizard.action_create_draft()

    def test_reviewed_draft_stays_unposted(self):
        company = self.env.company
        partner = self.env["res.partner"].create(
            {"name": "Proveedor RCV", "vat": "76123456-7"}
        )
        self.env["l10n_latam.document.type"].search(
            [("code", "=", "33"), ("country_id.code", "=", "CL")], limit=1
        )
        account = self.env["account.account"].create(
            {
                "name": "RCV reviewed expense",
                "code": "RCVEXP",
                "account_type": "expense",
                "company_ids": [(6, 0, company.ids)],
            }
        )
        product_category = self.env["product.category"].create(
            {
                "name": "RCV expense category",
                "property_account_expense_categ_id": account.id,
            }
        )
        product = self.env["product.product"].create(
            {
                "name": "RCV expense product",
                "categ_id": product_category.id,
                "purchase_ok": True,
                "type": "service",
            }
        )
        payable_account = self.env["account.account"].create(
            {
                "name": "RCV payable",
                "code": "RCVPAY",
                "account_type": "liability_payable",
                "reconcile": True,
                "company_ids": [(6, 0, company.ids)],
            }
        )
        partner.property_account_payable_id = payable_account
        analytic_plan = self.env["account.analytic.plan"].create(
            {"name": "RCV test plan"}
        )
        analytic_account = self.env["account.analytic.account"].create(
            {
                "name": "RCV test analytic",
                "plan_id": analytic_plan.id,
                "company_id": company.id,
            }
        )
        self.env["account.journal"].create(
            {
                "name": "RCV reviewed purchases",
                "code": "RCVR",
                "type": "purchase",
                "company_id": company.id,
            }
        )
        doc = self.env["l10n.cl.rcv.document"].create(
            {
                "company_id": company.id,
                "operation": "purchase",
                "tax_period": "202608",
                "document_type_code": "33",
                "issuer_vat": partner.vat,
                "folio": "936",
                "document_date": "2026-08-15",
                "net_amount": 100,
                "total_amount": 119,
                "reconciliation_state": "missing",
            }
        )
        wizard = self.env["l10n.cl.rcv.draft.wizard"].create(
            {
                "document_id": doc.id,
                "partner_id": partner.id,
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Reviewed purchase line",
                            "product_id": product.id,
                            "quantity": 1,
                            "price_unit": 100,
                            "analytic_distribution": {str(analytic_account.id): 100},
                        },
                    )
                ],
            }
        )
        action = wizard.action_create_draft()
        move = self.env["account.move"].browse(action["res_id"])
        self.assertEqual(move.state, "draft")
        self.assertEqual(move.l10n_latam_document_number.lstrip("0"), doc.folio)
        self.assertEqual(move.invoice_line_ids.account_id, account)
        self.assertEqual(
            move.invoice_line_ids.analytic_distribution,
            {str(analytic_account.id): 100},
        )
        self.assertEqual(doc.move_id, move)

        credit_note = self.env["l10n.cl.rcv.document"].create(
            {
                "company_id": company.id,
                "operation": "purchase",
                "tax_period": "202608",
                "document_type_code": "61",
                "issuer_vat": partner.vat,
                "folio": "937",
                "document_date": "2026-08-16",
                "net_amount": 100,
                "total_amount": 100,
                "reconciliation_state": "missing",
            }
        )
        credit_wizard = self.env["l10n.cl.rcv.draft.wizard"].create(
            {
                "document_id": credit_note.id,
                "partner_id": partner.id,
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Reviewed credit note line",
                            "quantity": 1,
                            "price_unit": 100,
                            "account_id": account.id,
                        },
                    )
                ],
            }
        )
        credit_action = credit_wizard.action_create_draft()
        credit_move = self.env["account.move"].browse(credit_action["res_id"])
        self.assertEqual(credit_move.move_type, "in_refund")
        self.assertEqual(credit_move.state, "draft")


class TestRcvPurchaseDteRounding(AccountTestInvoicingCommon):
    @classmethod
    @AccountTestInvoicingCommon.setup_country("cl")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.company.partner_id.vat = "78275587-0"
        cls.company.tax_calculation_rounding_method = "round_globally"
        cls.vendor = cls.env["res.partner"].create(
            {
                "name": "Banco de prueba",
                "vat": "97080000-K",
                "country_id": cls.env.ref("base.cl").id,
                "l10n_cl_sii_taxpayer_type": "1",
            }
        )
        cls.invoice_type = cls.env["l10n_latam.document.type"].search(
            [("code", "=", "33"), ("country_id.code", "=", "CL")], limit=1
        )
        recoverable_tag = cls.env.ref("l10n_cl.tag_cl_purchase_mnt_iva")
        cls.purchase_tax = cls.env["account.tax"].create(
            {
                "name": "RCV Test VAT 19%",
                "company_id": cls.company.id,
                "type_tax_use": "purchase",
                "amount_type": "percent",
                "amount": 19,
                "l10n_cl_sii_code": 14,
                "invoice_repartition_line_ids": [
                    (0, 0, {"repartition_type": "base"}),
                    (
                        0,
                        0,
                        {
                            "repartition_type": "tax",
                            "tag_ids": [(6, 0, recoverable_tag.ids)],
                        },
                    ),
                ],
                "refund_repartition_line_ids": [
                    (0, 0, {"repartition_type": "base"}),
                    (
                        0,
                        0,
                        {
                            "repartition_type": "tax",
                            "tag_ids": [(6, 0, recoverable_tag.ids)],
                        },
                    ),
                ],
            }
        )
        cls.expense_account = cls.company_data["default_account_expense"]
        cls.purchase_journal = cls.company_data["default_journal_purchase"]
        cls.env.user.groups_id |= cls.env.ref("l10n_cl_rcv.group_rcv_manager")
        cls.dte_test_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )

    @classmethod
    def _sign_test_dte(cls, xml):
        root = etree.fromstring(xml.encode())
        document = root.xpath('.//*[local-name()="Documento"]')[0]
        signed_info = root.xpath('.//*[local-name()="SignedInfo"]')[0]
        digest = base64.b64encode(
            hashlib.sha1(etree.tostring(document, method="c14n")).digest()
        ).decode()
        root.xpath('.//*[local-name()="DigestValue"]')[0].text = digest
        signed_bytes = etree.tostring(signed_info, method="c14n")
        signature = cls.dte_test_key.sign(
            signed_bytes, padding.PKCS1v15(), hashes.SHA1()
        )
        root.xpath('.//*[local-name()="SignatureValue"]')[0].text = base64.b64encode(
            signature
        ).decode()
        numbers = cls.dte_test_key.public_key().public_numbers()
        rsa_values = root.xpath('.//*[local-name()="RSAKeyValue"]')[0]
        rsa_values.xpath('./*[local-name()="Modulus"]')[0].text = base64.b64encode(
            numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
        ).decode()
        rsa_values.xpath('./*[local-name()="Exponent"]')[0].text = base64.b64encode(
            numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
        ).decode()
        return etree.tostring(root)

    def _purchase_dte(self, folio, vat=5614, xml_vat=5614, with_xml=True):
        move = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "company_id": self.company.id,
                "journal_id": self.purchase_journal.id,
                "partner_id": self.vendor.id,
                "invoice_date": "2026-10-05",
                "date": "2026-10-31",
                "l10n_latam_document_type_id": self.invoice_type.id,
                "l10n_latam_document_number": str(folio),
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Comisión bancaria 1",
                            "quantity": 1,
                            "price_unit": 14772,
                            "account_id": self.expense_account.id,
                            "tax_ids": [(6, 0, self.purchase_tax.ids)],
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "name": "Comisión bancaria 2",
                            "quantity": 1,
                            "price_unit": 14772,
                            "account_id": self.expense_account.id,
                            "tax_ids": [(6, 0, self.purchase_tax.ids)],
                        },
                    ),
                ],
            }
        )
        self.assertEqual(move.amount_untaxed, 29544)
        self.assertEqual(move.amount_tax, 5613)
        document = self.env["l10n.cl.rcv.document"].create(
            {
                "company_id": self.company.id,
                "operation": "purchase",
                "source": "sii",
                "tax_period": "202610",
                "document_type_code": "33",
                "issuer_vat": "97080000-K",
                "issuer_name": "BANCO BICE",
                "counterpart_vat": self.company.partner_id.vat,
                "folio": str(folio),
                "document_date": "2026-10-05",
                "sii_state": "REGISTRO",
                "net_amount": 29544,
                "exempt_amount": 0,
                "vat_amount": vat,
                "total_amount": 29544 + vat,
                "tax_breakdown_available": True,
                "reconciliation_state": "amount_diff",
            }
        )
        if with_xml:
            xml = f"""<DTE><Documento ID="DTE{folio}"><Encabezado>
                <IdDoc><TipoDTE>33</TipoDTE><Folio>{folio}</Folio>
                    <FchEmis>2026-10-05</FchEmis></IdDoc>
                <Emisor><RUTEmisor>97080000-K</RUTEmisor></Emisor>
                <Receptor><RUTRecep>{self.company.partner_id.vat}</RUTRecep></Receptor>
                <Totales><MntNeto>29544</MntNeto><MntExe>0</MntExe>
                    <TasaIVA>19</TasaIVA><IVA>{xml_vat}</IVA>
                    <MntTotal>{29544 + xml_vat}</MntTotal></Totales>
                </Encabezado>
                <Detalle><MontoItem>14772</MontoItem></Detalle>
                <Detalle><MontoItem>14772</MontoItem></Detalle>
                </Documento><Signature><SignedInfo>
                <CanonicalizationMethod Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315"/>
                <SignatureMethod Algorithm="http://www.w3.org/2000/09/xmldsig#rsa-sha1"/>
                <Reference URI="#DTE{folio}"><DigestMethod Algorithm="http://www.w3.org/2000/09/xmldsig#sha1"/>
                <DigestValue/></Reference></SignedInfo><SignatureValue/>
                <KeyInfo><KeyValue><RSAKeyValue><Modulus/><Exponent/></RSAKeyValue></KeyValue></KeyInfo>
                </Signature></DTE>"""
            signed_xml = self._sign_test_dte(xml)
            attachment = self.env["ir.attachment"].create(
                {
                    "name": f"DTE_{folio}.xml",
                    "type": "binary",
                    "mimetype": "application/xml",
                    "datas": base64.b64encode(signed_xml),
                    "res_model": "account.move",
                    "res_id": move.id,
                }
            )
            if "l10n_cl_dte_file" in move._fields:
                move.l10n_cl_dte_file = attachment
        return move, document

    def test_common_vat_without_xml_matches_fiscal_total_and_reports_allocation(self):
        move = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "company_id": self.company.id,
                "journal_id": self.purchase_journal.id,
                "partner_id": self.vendor.id,
                "invoice_date": "2026-10-05",
                "date": "2026-10-05",
                "l10n_latam_document_type_id": self.invoice_type.id,
                "l10n_latam_document_number": "98307",
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Servicio con IVA de uso común",
                            "quantity": 1,
                            "price_unit": 10000,
                            "account_id": self.expense_account.id,
                            "tax_ids": [(6, 0, self.purchase_tax.ids)],
                        },
                    )
                ],
            }
        )
        document = self.env["l10n.cl.rcv.document"].create(
            {
                "company_id": self.company.id,
                "operation": "purchase",
                "source": "sii",
                "tax_period": "202610",
                "document_type_code": "33",
                "issuer_vat": self.vendor.vat,
                "folio": "98307",
                "document_date": "2026-10-05",
                "net_amount": 10000,
                "vat_amount": 0,
                "common_vat": 1900,
                "total_amount": 11900,
                "tax_breakdown_available": True,
            }
        )

        document.action_reconcile()

        self.assertEqual(move.amount_tax, 1900)
        self.assertEqual(document.move_id, move)
        self.assertEqual(document.reconciliation_state, "matched")
        self.assertEqual(document.tax_comparison_state, "matched")
        self.assertEqual(document.tax_allocation_state, "different")
        self.assertIn("IVA de uso común", document.tax_allocation_note)

    def test_automatic_audit_is_idempotent_and_keeps_manual_events(self):
        move, document = self._purchase_dte("98307")
        log_model = self.env["l10n.cl.rcv.match.log"]
        decision = {"state": "matched", "reason_code": "fiscal_match"}
        evidence = {"source": "unit-test", "amount": 5613}

        log_model._log_automatic_decision(document, move, decision, evidence)
        log_model._log_automatic_decision(document, move, decision, evidence)
        manual = log_model.create(
            {
                "document_id": document.id,
                "move_id": move.id,
                "reason": "Revisión manual anterior",
                "event_type": "manual",
            }
        )

        self.assertEqual(len(document.automatic_log_ids), 1)
        self.assertIn(manual, document.match_log_ids)
        with self.assertRaises(UserError):
            document.automatic_log_ids.action_confirm()

    def test_line_rounded_source_dte_reconciles_without_changing_invoice(self):
        move, document = self._purchase_dte("3979263")
        before = (move.amount_tax, move.amount_total, move.line_ids.mapped("balance"))
        document.action_reconcile()

        self.assertEqual(document.reconciliation_state, "matched")
        self.assertEqual(document.tax_comparison_state, "matched")
        self.assertEqual(document.move_id, move)
        self.assertEqual(
            (move.amount_tax, move.amount_total, move.line_ids.mapped("balance")),
            before,
        )

    def test_unexplained_or_unsupported_rounding_stays_unmatched(self):
        cases = [
            ("wrong-tax", {"vat": 5615}),
            ("wrong-xml-tax", {"vat": 5615, "xml_vat": 5614}),
            ("missing-xml", {"vat": 5614, "with_xml": False}),
        ]
        for index, (folio, values) in enumerate(cases):
            with self.subTest(folio=folio):
                values.setdefault("vat", 5614)
                values.setdefault("xml_vat", 5614)
                values.setdefault("with_xml", True)
                move, document = self._purchase_dte(str(98000 + index), **values)
                document.action_reconcile()
                self.assertEqual(document.reconciliation_state, "amount_diff")
                self.assertEqual(move.amount_tax, 5613)

    def test_zero_rcv_vat_requires_matching_signed_dte_evidence(self):
        move, document = self._purchase_dte("98301", xml_vat=5613)
        document.write({"vat_amount": 0, "total_amount": 35157})
        self.assertTrue(
            self.env["l10n.cl.rcv.matcher"]._zero_vat_source_confirms(
                document, move, 29544, 0
            )
        )
        document.action_reconcile()
        self.assertEqual(document.reconciliation_state, "matched")
        self.assertEqual(document.tax_comparison_state, "source_confirmed")

        _move, missing_xml = self._purchase_dte("98302", with_xml=False)
        missing_xml.vat_amount = 0
        missing_xml.action_reconcile()
        self.assertEqual(missing_xml.reconciliation_state, "review")
        self.assertIn("IVA fiscal cero", missing_xml.automation_error)

        _move, mismatched_xml = self._purchase_dte("98303", xml_vat=5000)
        mismatched_xml.vat_amount = 0
        mismatched_xml.action_reconcile()
        self.assertEqual(mismatched_xml.reconciliation_state, "review")

        tampered_move, tampered_xml = self._purchase_dte("98306", xml_vat=5613)
        tampered_xml.vat_amount = 0
        attachment = tampered_move._l10n_cl_rcv_dte_attachment("l10n_cl_dte_file")
        changed_xml = attachment.raw.replace(b"<IVA>5613</IVA>", b"<IVA>5000</IVA>")
        attachment.write({"datas": base64.b64encode(changed_xml)})
        tampered_xml.action_reconcile()
        self.assertEqual(tampered_xml.reconciliation_state, "review")
        self.assertIn("IVA fiscal cero", tampered_xml.automation_error)

    def test_selected_reconcile_removes_only_proven_tma_from_draft(self):
        if not hasattr(
            self.env["fetchmail.server"],
            "_l10n_cl_rcv_repair_redundant_summary",
        ):
            raise unittest.SkipTest("requires l10n_cl_rcv_dte")
        move = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "company_id": self.company.id,
                "partner_id": self.vendor.id,
                "journal_id": self.purchase_journal.id,
                "invoice_date": "2026-10-05",
                "date": "2026-10-05",
                "l10n_latam_document_type_id": self.invoice_type.id,
                "l10n_latam_document_number": "98304",
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Servicio telefónico",
                            "quantity": 1,
                            "price_unit": 5034,
                            "account_id": self.expense_account.id,
                            "tax_ids": [(6, 0, self.purchase_tax.ids)],
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "name": "TOTAL MES ACTUAL",
                            "quantity": 1,
                            "price_unit": 5990,
                            "account_id": self.expense_account.id,
                            "tax_ids": [(6, 0, self.purchase_tax.ids)],
                        },
                    ),
                ],
            }
        )
        xml = f"""<DTE xmlns="http://www.sii.cl/SiiDte"><Documento>
            <Encabezado><IdDoc><TipoDTE>33</TipoDTE><Folio>98304</Folio></IdDoc>
            <Emisor><RUTEmisor>{self.vendor.vat}</RUTEmisor></Emisor>
            <Receptor><RUTRecep>{self.company.partner_id.vat}</RUTRecep></Receptor>
            <Totales><MntNeto>5034</MntNeto><MntExe>0</MntExe><TasaIVA>19</TasaIVA>
            <IVA>956</IVA><MntTotal>5990</MntTotal></Totales></Encabezado>
            <Detalle><CdgItem><TpoCodigo>INT1</TpoCodigo><VlrCodigo>SRV</VlrCodigo></CdgItem>
            <NmbItem>Servicio telefónico</NmbItem><MontoItem>5034</MontoItem></Detalle>
            <Detalle><CdgItem><TpoCodigo>INT1</TpoCodigo><VlrCodigo>TMA</VlrCodigo></CdgItem>
            <NmbItem>TOTAL MES ACTUAL</NmbItem><MontoItem>5990</MontoItem></Detalle>
            </Documento></DTE>"""
        attachment = self.env["ir.attachment"].create(
            {
                "name": "DTE_98304.xml",
                "type": "binary",
                "mimetype": "application/xml",
                "datas": base64.b64encode(xml.encode()),
                "res_model": "account.move",
                "res_id": move.id,
            }
        )
        if "l10n_cl_dte_file" in move._fields:
            move.l10n_cl_dte_file = attachment
        document = self.env["l10n.cl.rcv.document"].create(
            {
                "company_id": self.company.id,
                "operation": "purchase",
                "source": "sii",
                "tax_period": "202610",
                "document_type_code": "33",
                "issuer_vat": self.vendor.vat,
                "folio": "98304",
                "document_date": "2026-10-05",
                "net_amount": 5034,
                "vat_amount": 956,
                "total_amount": 5990,
                "tax_breakdown_available": True,
            }
        )

        document.action_reconcile()

        self.assertEqual(document.reconciliation_state, "matched")
        self.assertNotIn("TOTAL MES ACTUAL", move.invoice_line_ids.mapped("name"))
        self.assertEqual(move.amount_untaxed, 5034)
        self.assertEqual(move.amount_total, 5990)
        self.assertTrue(move.message_ids.filtered(lambda msg: "INT1/TMA" in msg.body))

        move.write(
            {
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "TOTAL MES ACTUAL",
                            "quantity": 1,
                            "price_unit": 5990,
                            "account_id": self.expense_account.id,
                            "tax_ids": [(6, 0, self.purchase_tax.ids)],
                        },
                    )
                ]
            }
        )
        move.action_post()
        posted_lines = move.invoice_line_ids.ids
        self.assertFalse(
            self.env["fetchmail.server"]._l10n_cl_rcv_repair_redundant_summary(move)
        )
        self.assertEqual(move.invoice_line_ids.ids, posted_lines)

    def test_purchase_credit_note_uses_reversal_vat_tag(self):
        normal_tag = self.env.ref("l10n_cl.tag_cl_purchase_mnt_iva")
        reversal_tag = normal_tag.copy(
            {"name": "-" + normal_tag.name, "tax_negate": True}
        )
        refund_tax_line = self.purchase_tax.refund_repartition_line_ids.filtered(
            lambda line: line.repartition_type == "tax"
        )
        refund_tax_line.tag_ids = [(6, 0, reversal_tag.ids)]
        note_type = self.env["l10n_latam.document.type"].search(
            [("code", "=", "61"), ("country_id.code", "=", "CL")], limit=1
        )
        move = self.env["account.move"].create(
            {
                "move_type": "in_refund",
                "company_id": self.company.id,
                "partner_id": self.vendor.id,
                "journal_id": self.purchase_journal.id,
                "invoice_date": "2026-10-05",
                "date": "2026-10-05",
                "l10n_latam_document_type_id": note_type.id,
                "l10n_latam_document_number": "98305",
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Ajuste",
                            "quantity": 1,
                            "price_unit": 10000,
                            "account_id": self.expense_account.id,
                            "tax_ids": [(6, 0, self.purchase_tax.ids)],
                        },
                    )
                ],
            }
        )
        document = self.env["l10n.cl.rcv.document"].create(
            {
                "company_id": self.company.id,
                "operation": "purchase",
                "source": "sii",
                "tax_period": "202610",
                "document_type_code": "61",
                "issuer_vat": self.vendor.vat,
                "folio": "98305",
                "document_date": "2026-10-05",
                "net_amount": 10000,
                "vat_amount": 1900,
                "total_amount": 11900,
                "tax_breakdown_available": True,
            }
        )

        document.action_reconcile()

        self.assertEqual(document.reconciliation_state, "matched")
        self.assertEqual(document.tax_comparison_state, "matched")
        self.assertEqual(document.move_id, move)

    def test_base_period_xml_identity_and_special_tax_mismatches_are_not_accepted(
        self,
    ):
        base_move, base_document = self._purchase_dte("98201")
        base_document.write({"net_amount": 29545, "total_amount": 35159})

        period_move, period_document = self._purchase_dte("98202")
        period_document.tax_period = "202609"

        identity_move, identity_document = self._purchase_dte("98203")
        attachment = identity_move._l10n_cl_rcv_dte_attachment("l10n_cl_dte_file")
        changed_xml = attachment.raw.replace(b"97080000-K", b"97080000-1")
        attachment.write({"datas": base64.b64encode(changed_xml)})

        special_move, special_document = self._purchase_dte("98204")
        attachment = special_move._l10n_cl_rcv_dte_attachment("l10n_cl_dte_file")
        changed_xml = attachment.raw.replace(
            b"</Documento>",
            b"<ImptoReten><MontoImp>1</MontoImp></ImptoReten></Documento>",
        )
        attachment.write({"datas": base64.b64encode(changed_xml)})

        for move, document in (
            (base_move, base_document),
            (period_move, period_document),
            (identity_move, identity_document),
            (special_move, special_document),
        ):
            with self.subTest(folio=document.folio):
                document.action_reconcile()
                expected_state = (
                    "matched" if document == period_document else "amount_diff"
                )
                self.assertEqual(document.reconciliation_state, expected_state)
                self.assertEqual(move.amount_tax, 5613)

    def test_selected_action_reevaluates_only_selected_rounding_cases(self):
        matched_move, matched = self._purchase_dte("98101")
        unmatched_move, unmatched = self._purchase_dte("98102", vat=5615)
        _unselected_move, unselected = self._purchase_dte("98103")
        matched.write({"move_id": False, "reconciliation_state": "amount_diff"})
        unselected.write({"move_id": False, "reconciliation_state": "pending"})
        action = self.env.ref("l10n_cl_rcv.action_rcv_reconcile_selected")

        action.with_context(
            active_model="l10n.cl.rcv.document",
            active_ids=[matched.id, unmatched.id],
        ).run()

        self.assertEqual(matched.move_id, matched_move)
        self.assertEqual(matched.reconciliation_state, "matched")
        self.assertEqual(unmatched.move_id, unmatched_move)
        self.assertEqual(unmatched.reconciliation_state, "amount_diff")
        self.assertEqual(unselected.reconciliation_state, "pending")
        self.assertFalse(unselected.move_id)


class TestRcvExportReconciliation(AccountTestInvoicingCommon):
    @classmethod
    @AccountTestInvoicingCommon.setup_country("cl")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.company.partner_id.vat = "78275587-0"
        cls.company.tax_calculation_rounding_method = "round_globally"
        cls.env.user.groups_id |= cls.env.ref("l10n_cl_rcv.group_rcv_manager")
        cls.customer = cls.env["res.partner"].create(
            {
                "name": "Export customer",
                "vat": "PY-80122338-5",
                "country_id": cls.env.ref("base.py").id,
                "l10n_cl_sii_taxpayer_type": "4",
            }
        )
        cls.export_type = cls.env.ref("l10n_cl.dc_fe_dte")
        cls.export_type.active = True
        cls.export_journal = cls.company_data["default_journal_sale"]
        journal_values = {"l10n_latam_use_documents": True}
        if "l10n_cl_point_of_sale_type" in cls.export_journal._fields:
            journal_values["l10n_cl_point_of_sale_type"] = "manual"
        cls.export_journal.write(journal_values)
        cls.usd = cls.env.ref("base.USD")
        cls.usd.active = True
        cls.env["res.currency.rate"].create(
            {
                "currency_id": cls.usd.id,
                "company_id": cls.company.id,
                "name": "2026-09-01",
                "rate": 0.001,
            }
        )

    def _export(self, folio="66", posted=True, taxes=None):
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": self.export_journal.id,
                "partner_id": self.customer.id,
                "currency_id": self.usd.id,
                "invoice_date": "2026-09-24",
                "date": "2026-09-24",
                "l10n_latam_document_type_id": self.export_type.id,
                "l10n_latam_document_number": folio,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Export service",
                            "quantity": 1,
                            "price_unit": 4080,
                            "account_id": self.company_data[
                                "default_account_revenue"
                            ].id,
                            "tax_ids": [(6, 0, (taxes or self.env["account.tax"]).ids)],
                        },
                    )
                ],
            }
        )
        if posted:
            move.action_post()
        document = self.env["l10n.cl.rcv.document"].create(
            {
                "company_id": self.company.id,
                "operation": "sale",
                "source": "sii",
                "tax_period": "202609",
                "document_type_code": "110",
                "issuer_vat": "78.275.587-0",
                "counterpart_vat": "CL 55.555.555-5",
                "folio": folio,
                "document_date": "2026-09-24",
                "exempt_amount": abs(move.amount_total_signed),
                "total_amount": abs(move.amount_total_signed),
                "tax_breakdown_available": True,
            }
        )
        return move, document

    def _exempt_dte61(self, folio="701", source_taxes=None):
        customer = self.env["res.partner"].create(
            {
                "name": "Domestic exempt customer",
                "vat": "76123456-0",
                "country_id": self.env.ref("base.cl").id,
                "l10n_cl_sii_taxpayer_type": "1",
            }
        )
        source = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": self.export_journal.id,
                "partner_id": customer.id,
                "currency_id": self.company.currency_id.id,
                "invoice_date": "2026-09-13",
                "date": "2026-09-13",
                "l10n_latam_document_type_id": self.env.ref("l10n_cl.dc_y_f_dte").id,
                "l10n_latam_document_number": "231",
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Exempt service",
                            "quantity": 1,
                            "price_unit": 20_700_020,
                            "account_id": self.company_data[
                                "default_account_revenue"
                            ].id,
                            "tax_ids": [
                                (6, 0, (source_taxes or self.env["account.tax"]).ids)
                            ],
                        },
                    )
                ],
            }
        )
        source.action_post()
        wizard = (
            self.env["account.move.reversal"]
            .with_context(active_model="account.move", active_ids=source.ids)
            .create(
                {
                    "reason": "Exempt DTE 61 RCV reconciliation test",
                    "journal_id": source.journal_id.id,
                }
            )
        )
        refund = self.env["account.move"].browse(wizard.refund_moves()["res_id"])
        refund.write(
            {
                "date": "2026-09-14",
                "invoice_date": "2026-09-14",
                "l10n_latam_document_number": folio,
            }
        )
        refund.action_post()
        document = self.env["l10n.cl.rcv.document"].create(
            {
                "company_id": self.company.id,
                "operation": "sale",
                "source": "sii",
                "tax_period": "202609",
                "document_type_code": "61",
                "issuer_vat": self.company.partner_id.vat,
                "counterpart_vat": customer.vat,
                "folio": folio,
                "document_date": "2026-09-14",
                "net_amount": 0,
                "exempt_amount": abs(refund.amount_total_signed),
                "vat_amount": 0,
                "total_amount": abs(refund.amount_total_signed),
                "tax_breakdown_available": False,
                "reconciliation_state": "review",
            }
        )
        return source, refund, document

    def test_server_action_reconciles_only_selected_documents(self):
        move, matched_document = self._export(folio="681")
        matched_document.write({"move_id": False, "reconciliation_state": "missing"})
        unmatched_document = self.env["l10n.cl.rcv.document"].create(
            {
                "company_id": self.company.id,
                "operation": "sale",
                "source": "sii",
                "tax_period": "202609",
                "document_type_code": "33",
                "issuer_vat": "78275587-0",
                "counterpart_vat": self.customer.vat,
                "folio": "999999",
                "document_date": "2026-09-24",
                "sii_state": "REGISTRO",
                "total_amount": 119,
                "reconciliation_state": "pending",
            }
        )
        unselected_document = unmatched_document.copy(
            {
                "folio": "999998",
                "reconciliation_state": "review",
                "automation_error": "Conservar este diagnóstico",
            }
        )
        action = self.env.ref("l10n_cl_rcv.action_rcv_reconcile_selected")

        self.assertEqual(action.model_id.model, "l10n.cl.rcv.document")
        self.assertEqual(action.binding_model_id, action.model_id)
        self.assertEqual(action.binding_view_types, "list")
        self.assertIn(self.env.ref("l10n_cl_rcv.group_rcv_manager"), action.groups_id)
        result = action.with_context(
            active_model="l10n.cl.rcv.document",
            active_ids=[matched_document.id, unmatched_document.id],
        ).run()

        self.assertEqual(result["tag"], "display_notification")
        self.assertEqual(result["params"]["next"]["tag"], "soft_reload")
        self.assertIn("1 conciliados", result["params"]["message"])
        self.assertEqual(matched_document.move_id, move)
        self.assertEqual(matched_document.reconciliation_state, "matched")
        self.assertEqual(unmatched_document.reconciliation_state, "missing")
        self.assertEqual(unselected_document.reconciliation_state, "review")
        self.assertEqual(
            unselected_document.automation_error, "Conservar este diagnóstico"
        )

        non_manager = self.env["res.users"].create(
            {
                "name": "RCV user without manager access",
                "login": "rcv-no-manager",
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            }
        )
        with self.assertRaises(AccessError):
            action.with_user(non_manager).with_context(
                active_model="l10n.cl.rcv.document",
                active_ids=[unselected_document.id],
            ).run()

    def test_native_export_matches_company_currency_and_repeats(self):
        move, document = self._export()
        document.write({"move_id": move.id, "reconciliation_state": "missing"})
        before = (move.write_date, move.name, move.line_ids.mapped("balance"))
        customer_vat = self.customer.vat
        # Changing the current rate must not revalue the posted invoice.
        self.env["res.currency.rate"].create(
            {
                "currency_id": self.usd.id,
                "company_id": self.company.id,
                "name": "2026-10-19",
                "rate": 0.002,
            }
        )
        for _iteration in range(2):
            document.action_reconcile()
            self.assertEqual(document.move_id, move)
            self.assertEqual(document.reconciliation_state, "matched")
        self.assertEqual(
            before, (move.write_date, move.name, move.line_ids.mapped("balance"))
        )
        self.assertEqual(self.customer.vat, customer_vat)

    def test_export_identity_guards_and_normalized_folio(self):
        move, document = self._export()
        matcher = self.env["l10n.cl.rcv.matcher"]
        document.folio = "000066"
        self.assertEqual(matcher._candidate_moves(document), move)
        for values, expected in (
            ({"issuer_vat": "76123456-0"}, "review"),
            ({"issuer_vat": "78275587-0", "folio": "bad"}, "review"),
            ({"folio": "999"}, "missing"),
            ({"folio": "66", "counterpart_vat": "76123456-0"}, "missing"),
        ):
            document.write(values)
            document.action_reconcile()
            self.assertEqual(document.reconciliation_state, expected)
        document.write({"counterpart_vat": "55555555-5", "operation": "purchase"})
        self.assertFalse(matcher._candidate_moves(document))
        document.write({"operation": "sale", "document_type_code": "34"})
        self.assertFalse(matcher._candidate_moves(document))

    def test_export_amount_period_and_tax_evidence(self):
        move, document = self._export()
        document.total_amount += 10
        document.action_reconcile()
        self.assertEqual(document.reconciliation_state, "amount_diff")
        document.total_amount -= 10
        document.tax_period = "202610"
        document.action_reconcile()
        self.assertEqual(document.reconciliation_state, "period_diff")
        self.assertEqual(str(move.date), "2026-09-24")
        document.write({"tax_period": "202609", "tax_breakdown_available": False})
        document.action_reconcile()
        self.assertEqual(document.reconciliation_state, "matched")
        document.write({"tax_breakdown_available": True, "vat_amount": 1})
        document.action_reconcile()
        self.assertEqual(document.reconciliation_state, "amount_diff")

    def test_export_zero_tax_without_optional_rcv_tax_fields_matches(self):
        move, document = self._export()
        document.write(
            {
                "tax_breakdown_available": False,
                "net_amount": 0,
                "exempt_amount": abs(move.amount_total_signed),
                "vat_amount": 0,
                "total_amount": abs(move.amount_total_signed),
            }
        )

        document.action_reconcile()

        self.assertEqual(document.reconciliation_state, "matched")
        self.assertEqual(document.tax_comparison_state, "matched")
        self.assertFalse(document.automation_error)

    def test_exempt_dte61_reversal_matches_when_source_proves_exemption(self):
        source, refund, document = self._exempt_dte61(folio="22")
        document.action_reconcile()

        self.assertEqual(document.move_id, refund)
        self.assertEqual(document.reconciliation_state, "matched")
        self.assertEqual(document.tax_comparison_state, "matched")
        self.assertEqual(source.state, "posted")
        self.assertEqual(refund.state, "posted")

    def test_selected_action_reconciles_existing_exempt_dte61(self):
        _source, refund, document = self._exempt_dte61(folio="22")
        document.write({"move_id": False, "reconciliation_state": "review"})
        unrelated = document.copy(
            {
                "folio": "703",
                "reconciliation_state": "review",
                "automation_error": "Keep this unrelated diagnostic",
            }
        )
        action = self.env.ref("l10n_cl_rcv.action_rcv_reconcile_selected")

        action.with_context(
            active_model="l10n.cl.rcv.document", active_ids=[document.id]
        ).run()

        self.assertEqual(document.move_id, refund)
        self.assertEqual(document.reconciliation_state, "matched")
        self.assertEqual(unrelated.reconciliation_state, "review")
        self.assertEqual(unrelated.automation_error, "Keep this unrelated diagnostic")

    def test_exempt_dte61_rejects_taxed_source(self):
        vat = self.env["account.tax"].create(
            {
                "name": "Taxed source for DTE 61 guard",
                "amount": 19,
                "amount_type": "percent",
                "type_tax_use": "sale",
                "company_id": self.company.id,
                "tax_group_id": self.env["account.tax.group"].search([], limit=1).id,
            }
        )
        _source, _refund, document = self._exempt_dte61(folio="704", source_taxes=vat)

        document.action_reconcile()

        self.assertEqual(document.reconciliation_state, "amount_diff")

    def test_exempt_dte61_requires_matching_period_and_amount(self):
        _source, _refund, document = self._exempt_dte61(folio="705")
        document.tax_period = "202610"

        document.action_reconcile()

        self.assertEqual(document.reconciliation_state, "review")
        document.write({"tax_period": "202609", "total_amount": 20_700_021})
        document.action_reconcile()
        self.assertEqual(document.reconciliation_state, "amount_diff")

    def test_exempt_dte61_requires_a_posted_type34_reversal_source(self):
        source, _refund, document = self._exempt_dte61(folio="706")
        source.l10n_latam_document_type_id = self.env.ref("l10n_cl.dc_a_f_dte")

        document.action_reconcile()

        self.assertEqual(document.reconciliation_state, "review")

    def test_exempt_dte61_requires_reversal_link_and_posted_credit_note(self):
        _source, refund, document = self._exempt_dte61(folio="707")
        refund.reversed_entry_id = False

        document.action_reconcile()

        self.assertEqual(document.reconciliation_state, "review")
        refund.button_draft()
        document.action_reconcile()
        self.assertEqual(document.reconciliation_state, "review")

    def test_export_positive_vat_without_breakdown_stays_manual(self):
        move, document = self._export()
        document.write(
            {
                "tax_breakdown_available": False,
                "net_amount": abs(move.amount_total_signed),
                "exempt_amount": 0,
                "vat_amount": 1,
                "total_amount": abs(move.amount_total_signed),
            }
        )

        document.action_reconcile()

        self.assertEqual(document.reconciliation_state, "review")
        self.assertTrue(document.automation_error)

    def test_export_foreign_currency_uses_posted_company_balances(self):
        move, document = self._export(taxes=self.env["account.tax"])
        document.write(
            {
                "tax_breakdown_available": False,
                "net_amount": 0,
                "exempt_amount": abs(move.amount_total_signed),
                "vat_amount": 0,
                "total_amount": abs(move.amount_total_signed),
            }
        )

        document.action_reconcile()

        self.assertEqual(document.reconciliation_state, "matched")
        self.assertEqual(document.total_amount, abs(move.amount_total_signed))

    def test_export_zero_tax_exception_requires_matching_period(self):
        move, document = self._export()
        document.write(
            {
                "tax_breakdown_available": False,
                "net_amount": 0,
                "exempt_amount": abs(move.amount_total_signed),
                "vat_amount": 0,
                "total_amount": abs(move.amount_total_signed),
                "tax_period": "202610",
            }
        )

        document.action_reconcile()

        self.assertEqual(document.reconciliation_state, "period_diff")

    def test_ordinary_exempt_invoice_rechecks_stale_missing_state(self):
        document_type = self.env["l10n_latam.document.type"].search(
            [("code", "=", "34"), ("country_id.code", "=", "CL")], limit=1
        )
        partner = self.env["res.partner"].create(
            {
                "name": "Exempt customer",
                "vat": "76123456-0",
                "country_id": self.env.ref("base.cl").id,
                "l10n_cl_sii_taxpayer_type": "1",
            }
        )
        zero_tax = self.env["account.tax"].create(
            {
                "name": "Exempt sale",
                "amount": 0,
                "amount_type": "percent",
                "type_tax_use": "sale",
                "company_id": self.company.id,
                "tax_group_id": self.env["account.tax.group"].search([], limit=1).id,
            }
        )
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": self.export_journal.id,
                "partner_id": partner.id,
                "invoice_date": "2026-09-24",
                "date": "2026-09-24",
                "l10n_latam_document_type_id": document_type.id,
                "l10n_latam_document_number": "242",
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Exempt service",
                            "quantity": 1,
                            "price_unit": 1000,
                            "account_id": self.company_data[
                                "default_account_revenue"
                            ].id,
                            "tax_ids": [(6, 0, zero_tax.ids)],
                        },
                    )
                ],
            }
        )
        move.action_post()
        document = self.env["l10n.cl.rcv.document"].create(
            {
                "company_id": self.company.id,
                "operation": "sale",
                "source": "sii",
                "tax_period": "202609",
                "document_type_code": "34",
                "issuer_vat": self.company.partner_id.vat,
                "counterpart_vat": partner.vat,
                "folio": "242",
                "document_date": "2026-09-24",
                "net_amount": 0,
                "exempt_amount": abs(move.amount_total_signed),
                "vat_amount": 0,
                "total_amount": abs(move.amount_total_signed),
                "tax_breakdown_available": False,
                "reconciliation_state": "missing",
            }
        )

        document.action_reconcile()

        self.assertEqual(document.move_id, move)
        self.assertEqual(document.reconciliation_state, "matched")

    def test_exempt_dte34_foreign_currency_uses_company_balances(self):
        document_type = self.env["l10n_latam.document.type"].search(
            [("code", "=", "34"), ("country_id.code", "=", "CL")], limit=1
        )
        partner = self.env["res.partner"].create(
            {
                "name": "Foreign exempt customer",
                "vat": "76123456-0",
                "country_id": self.env.ref("base.cl").id,
                "l10n_cl_sii_taxpayer_type": "1",
            }
        )
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": self.export_journal.id,
                "partner_id": partner.id,
                "currency_id": self.usd.id,
                "invoice_date": "2026-09-24",
                "date": "2026-09-24",
                "l10n_latam_document_type_id": document_type.id,
                "l10n_latam_document_number": "243",
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Foreign exempt service",
                            "quantity": 1,
                            "price_unit": 4080,
                            "account_id": self.company_data[
                                "default_account_revenue"
                            ].id,
                            "tax_ids": [(5, 0, 0)],
                        },
                    )
                ],
            }
        )
        move.action_post()
        document = self.env["l10n.cl.rcv.document"].create(
            {
                "company_id": self.company.id,
                "operation": "sale",
                "source": "sii",
                "tax_period": "202609",
                "document_type_code": "34",
                "issuer_vat": self.company.partner_id.vat,
                "counterpart_vat": partner.vat,
                "folio": "243",
                "document_date": "2026-09-24",
                "net_amount": 0,
                "exempt_amount": abs(move.amount_total_signed),
                "vat_amount": 0,
                "total_amount": abs(move.amount_total_signed),
                "tax_breakdown_available": False,
            }
        )

        document.action_reconcile()

        self.assertEqual(document.reconciliation_state, "matched")
        self.assertEqual(document.total_amount, abs(move.amount_total_signed))

    def test_export_draft_duplicate_cancel_and_conflicting_link(self):
        move, document = self._export(posted=False)
        original_date = move.date
        document.tax_period = "202610"
        document.action_reconcile()
        self.assertEqual(document.reconciliation_state, "review")
        self.assertEqual(move.date, original_date)
        other = move.copy({"l10n_latam_document_number": "66"})
        document.action_reconcile()
        self.assertEqual(document.reconciliation_state, "duplicate")
        other.button_cancel()
        self.assertEqual(
            self.env["l10n.cl.rcv.matcher"]._candidate_moves(document), move
        )
        other.button_draft()
        other.l10n_latam_document_number = "67"
        document.move_id = other
        document.action_reconcile()
        self.assertEqual(document.reconciliation_state, "review")
        self.assertEqual(document.move_id, other)

    def test_export_other_company_is_not_a_candidate(self):
        move, document = self._export()
        other_company = self.env["res.company"].create({"name": "Other exporter"})
        document.write({"company_id": other_company.id, "issuer_vat": "76123456-0"})
        other_company.partner_id.vat = "76123456-0"
        self.assertFalse(self.env["l10n.cl.rcv.matcher"]._candidate_moves(document))

    def test_export_zero_tax_and_unidentified_tax(self):
        zero_tax = self.env["account.tax"].create(
            {
                "name": "Export exempt",
                "amount": 0,
                "amount_type": "percent",
                "type_tax_use": "sale",
                "company_id": self.company.id,
                "tax_group_id": self.env["account.tax.group"].search([], limit=1).id,
            }
        )
        move, document = self._export(taxes=zero_tax)
        document.action_reconcile()
        self.assertEqual(document.reconciliation_state, "matched")
        document.write({"exempt_amount": 0, "net_amount": document.total_amount})
        document.action_reconcile()
        self.assertEqual(document.reconciliation_state, "amount_diff")
        vat = self.env["account.tax"].create(
            {
                "name": "Unidentified tax",
                "amount": 19,
                "amount_type": "percent",
                "type_tax_use": "sale",
                "company_id": self.company.id,
                "tax_group_id": self.env["account.tax.group"].search([], limit=1).id,
            }
        )
        taxable_move, taxable_doc = self._export(folio="67", taxes=vat)
        taxable_doc.write(
            {
                "net_amount": abs(taxable_move.amount_untaxed_signed),
                "exempt_amount": 0,
            }
        )
        taxable_doc.action_reconcile()
        self.assertEqual(taxable_doc.reconciliation_state, "review")

    def test_export_observed_on_invoice_during_sync(self):
        move, document = self._export()
        period = self.env["l10n.cl.rcv.period"].create(
            {
                "company_id": self.company.id,
                "year": 2026,
                "month": 9,
                "operation": "sale",
            }
        )
        sync = self.env["l10n.cl.rcv.sync"].create(
            {
                "company_id": self.company.id,
                "period_id": period.id,
                "operation": "sale",
                "requested_by": self.env.user.id,
            }
        )
        matcher = self.env["l10n.cl.rcv.matcher"]
        matcher._reconcile_moves(sync)
        self.assertEqual(move.l10n_cl_rcv_reconciliation_state, "matched")
        document.issuer_vat = "76123456-0"
        matcher._reconcile_moves(sync)
        self.assertEqual(move.l10n_cl_rcv_reconciliation_state, "indeterminate")
