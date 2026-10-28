# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Exercise the RCV button without sending requests to SII."""

import base64
import unittest
from datetime import date
from unittest.mock import patch

from lxml import etree

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.l10n_cl_dte_mipyme_recovery.services.mipyme_adapter import (
    MipymeAdapterError,
    PortalChunk,
    ProductionMipymeAdapter,
)
from odoo.addons.l10n_cl_dte_mipyme_recovery.services.recovery_service import (
    RecoveryResult,
)
from odoo.addons.l10n_cl_dte_mipyme_recovery.services.xml_dte import extract_candidates

try:
    from odoo.addons.l10n_cl_edi.tests.common import TestL10nClEdiCommon

    HAS_DTE_LOCALIZATION = True
except ImportError:  # l10n_cl_edi is an optional, non-OCA dependency.
    TestL10nClEdiCommon = TransactionCase
    HAS_DTE_LOCALIZATION = False


@tagged("post_install", "-at_install")
@unittest.skipUnless(HAS_DTE_LOCALIZATION, "requires l10n_cl_edi")
class TestRcvMipymeImport(TestL10nClEdiCommon):
    def setUp(self):
        super().setUp()
        self.company = self.company_data["company"]
        self.company.partner_id.vat = "76543210-3"
        self.customer = self.env["res.partner"].create(
            {
                "name": "Cliente histórico",
                "vat": "96961400-6",
                "country_id": self.env.ref("base.cl").id,
                "l10n_latam_identification_type_id": self.env.ref("l10n_cl.it_RUT").id,
                "l10n_cl_sii_taxpayer_type": "1",
            }
        )
        self.env.user.write(
            {
                "groups_id": [
                    (4, self.env.ref("l10n_cl_rcv.group_rcv_manager").id),
                    (
                        4,
                        self.env.ref(
                            "l10n_cl_dte_mipyme_recovery.group_mipyme_recovery_user"
                        ).id,
                    ),
                ]
            }
        )

    def _document(self, code="34", folio="224", **values):
        amounts = (
            {"net_amount": 100, "vat_amount": 19, "total_amount": 119}
            if code in ("33", "56")
            else {"exempt_amount": 100, "total_amount": 100}
        )
        return self.env["l10n.cl.rcv.document"].create(
            {
                "company_id": self.company.id,
                "operation": "sale",
                "source": "sii",
                "tax_period": "202608",
                "document_type_code": code,
                "issuer_vat": "76543210-3",
                "counterpart_vat": (
                    "55555555-5" if code in ("110", "111") else self.customer.vat
                ),
                "folio": folio,
                "document_date": date(2026, 8, 20),
                "reconciliation_state": "missing",
                "tax_breakdown_available": True,
                **amounts,
                **values,
            }
        )

    @staticmethod
    def _xml(code="34", folio="224", recipient=None, currency="PESO CL"):
        recipient = recipient or (
            "55555555-5" if code in ("110", "111") else "96961400-6"
        )
        totals = (
            "<MntNeto>100</MntNeto><IVA>19</IVA><MntTotal>119</MntTotal>"
            if code in ("33", "56")
            else "<MntExe>100</MntExe><MntTotal>100</MntTotal>"
        )
        reference = (
            "<Referencia><TpoDocRef>33</TpoDocRef><FolioRef>12</FolioRef>"
            "<FchRef>2026-08-01</FchRef><CodRef>3</CodRef></Referencia>"
            if code in ("56", "111")
            else ""
        )
        foreign = (
            "<Extranjero><NumId>US-123456</NumId>"
            "<Nacionalidad>225</Nacionalidad></Extranjero>"
            if code in ("110", "111")
            else ""
        )
        currency_xml = (
            f"<TpoMoneda>{currency}</TpoMoneda>" if code in ("110", "111") else ""
        )
        return f"""<DTE xmlns="http://www.sii.cl/SiiDte"><Documento>
          <Encabezado><IdDoc><TipoDTE>{code}</TipoDTE><Folio>{folio}</Folio>
          <FchEmis>2026-08-20</FchEmis></IdDoc>
          <Emisor><RUTEmisor>76543210-3</RUTEmisor></Emisor>
          <Receptor><RUTRecep>{recipient}</RUTRecep>
          <RznSocRecep>Cliente histórico</RznSocRecep>{foreign}</Receptor>
          <Totales>{currency_xml}{totals}</Totales></Encabezado>
          <Detalle><NmbItem>Servicio histórico</NmbItem><QtyItem>1</QtyItem>
          <MontoItem>100</MontoItem></Detalle>{reference}<TED/><Signature/>
          </Documento></DTE>""".encode()

    @staticmethod
    def _folio_61_xml(
        folio="61",
        recipient_id="801223385",
        recipient_name="SIOM PARAGUAY S.A.",
        country_code="",
    ):
        declared_country = (
            f"<Nacionalidad>{country_code}</Nacionalidad>" if country_code else ""
        )
        return f"""<DTE xmlns="http://www.sii.cl/SiiDte"><Documento>
          <Encabezado><IdDoc><TipoDTE>110</TipoDTE><Folio>{folio}</Folio>
          <FchEmis>2026-09-06</FchEmis></IdDoc>
          <Emisor><RUTEmisor>78275587-0</RUTEmisor></Emisor>
          <Receptor><RUTRecep>55555555-5</RUTRecep>
          <RznSocRecep>{recipient_name}</RznSocRecep>
          <Extranjero><NumId>{recipient_id}</NumId>{declared_country}</Extranjero>
          </Receptor><Totales><TpoMoneda>DOLAR USA</TpoMoneda><MntExe>4080</MntExe>
          <MntTotal>4080</MntTotal></Totales>
          <OtraMoneda><TpoMoneda>PESO CL</TpoMoneda><TpoCambio>933.47</TpoCambio>
          <MntExeOtrMnda>3808558</MntExeOtrMnda>
          <MntTotOtrMnda>3808558</MntTotOtrMnda></OtraMoneda></Encabezado>
          <Detalle><NmbItem>FLETE INTERNACIONAL</NmbItem><QtyItem>1</QtyItem>
          <PrcItem>4080</PrcItem><MontoItem>4080</MontoItem></Detalle>
          <TED/><Signature/></Documento></DTE>""".encode()

    def _set_folio_61_usd_rate(self):
        usd = self.env.ref("base.USD")
        usd.active = True
        rate_values = {
            "currency_id": usd.id,
            "company_id": self.company.id,
            "name": "2026-09-06",
            "company_rate": 1 / 933.47,
        }
        rate = self.env["res.currency.rate"].search(
            [
                ("currency_id", "=", usd.id),
                ("company_id", "=", self.company.id),
                ("name", "=", "2026-09-06"),
            ],
            limit=1,
        )
        if rate:
            rate.write(rate_values)
        else:
            self.env["res.currency.rate"].create(rate_values)
        self.assertEqual(
            usd._convert(
                4080, self.company.currency_id, self.company, date(2026, 9, 6)
            ),
            3808558,
        )
        return usd

    def _paraguay_customer(self, name="SIOM Paraguay S.A.", vat="80122338-5"):
        return self.env["res.partner"].create(
            {
                "name": name,
                "vat": vat,
                "country_id": self.env.ref("base.py").id,
                "company_type": "company",
                "l10n_cl_sii_taxpayer_type": "4",
            }
        )

    def _import_folio_61(self, payload):
        self.company.partner_id.vat = "78275587-0"
        document = self._document(
            "110",
            "61",
            issuer_vat="78275587-0",
            document_date=date(2026, 9, 6),
            tax_period="202609",
            exempt_amount=3808558,
            total_amount=3808558,
        )
        with patch.object(
            ProductionMipymeAdapter,
            "retrieve",
            return_value=[PortalChunk(date(2026, 9, 6), date(2026, 9, 6), 1, payload)],
        ):
            action = document.action_import_from_mipyme()
        return document, action

    def test_one_click_opens_draft_and_reconciles(self):
        for code, folio in (
            ("33", "224"),
            ("34", "225"),
            ("39", "226"),
            ("41", "227"),
            ("56", "228"),
            ("110", "229"),
            ("111", "230"),
        ):
            document = self._document(code, folio)
            payload = self._xml(code, folio)
            with patch.object(
                ProductionMipymeAdapter,
                "retrieve",
                return_value=[
                    PortalChunk(date(2026, 8, 20), date(2026, 8, 20), 1, payload)
                ],
            ) as retrieve:
                action = document.action_import_from_mipyme()
            self.assertEqual(
                action["res_model"],
                "account.move",
                (
                    code,
                    self.env["l10n_cl.dte.recovery"]
                    .browse(action["res_id"])
                    .diagnostic,
                )
                if action["res_model"] == "l10n_cl.dte.recovery"
                else code,
            )
            move = self.env["account.move"].browse(action["res_id"])
            self.assertEqual(move.state, "draft")
            self.assertEqual(move.move_type, "out_invoice")
            self.assertEqual(move.l10n_latam_document_type_id.code, code)
            self.assertTrue(move.l10n_cl_mipyme_imported)
            self.assertEqual(document.move_id, move)
            self.assertEqual(
                document.reconciliation_state, "review" if code == "110" else "matched"
            )
            self.assertEqual(
                retrieve.call_args.args[-2:], (date(2026, 8, 20), date(2026, 8, 20))
            )
            if code in ("56", "111"):
                self.assertEqual(move.l10n_cl_reference_ids.origin_doc_number, "12")
            if code in ("110", "111"):
                self.assertEqual(move.partner_id.vat, "US-123456")
                self.assertEqual(move.partner_id.country_id.code, "US")
            with patch.object(ProductionMipymeAdapter, "retrieve") as repeat_retrieve:
                repeat = document.action_import_from_mipyme()
            self.assertEqual(repeat["res_id"], move.id)
            repeat_retrieve.assert_not_called()

    def test_missing_date_uses_rcv_month(self):
        document = self._document(document_date=False)
        self.assertEqual(
            document._mipyme_search_dates(),
            (date(2026, 8, 1), date(2026, 8, 31)),
        )

    def test_purchase_and_credit_note_are_not_eligible(self):
        purchase = self._document(operation="purchase")
        self.assertFalse(purchase.mipyme_import_available)
        with self.assertRaises(UserError):
            purchase.action_import_from_mipyme()
        credit = self._document("61", "226")
        self.assertFalse(credit.mipyme_import_available)

    def test_form_has_direct_sales_action_and_existing_purchase_action(self):
        view = self.env["l10n.cl.rcv.document"].get_view(view_type="form")
        root = etree.fromstring(view["arch"].encode())
        sales_buttons = root.xpath('//button[@name="action_import_from_mipyme"]')
        self.assertEqual(len(sales_buttons), 1)
        self.assertEqual(sales_buttons[0].get("type"), "object")
        self.assertEqual(
            sales_buttons[0].get("invisible"), "not mipyme_import_available"
        )
        self.assertEqual(len(root.xpath('//button[@name="action_create_draft"]')), 1)

    def test_all_chilean_out_invoice_types_are_eligible(self):
        for code in ("33", "34", "39", "41", "43", "56", "110", "111"):
            with self.subTest(code=code):
                self.assertTrue(
                    self._document(code, str(300 + int(code))).mipyme_import_available
                )
        for code in ("46", "52", "61", "112"):
            with self.subTest(code=code):
                self.assertFalse(
                    self._document(code, str(500 + int(code))).mipyme_import_available
                )

    def test_inactive_historical_type_is_reviewable(self):
        document_type = self.env["l10n_latam.document.type"].search(
            [("code", "=", "43"), ("country_id.code", "=", "CL")],
            limit=1,
        )
        document_type.active = False
        document = self._document("43", "343")
        self.assertTrue(document.mipyme_import_available)
        with patch.object(
            ProductionMipymeAdapter,
            "retrieve",
            return_value=[
                PortalChunk(
                    date(2026, 8, 20), date(2026, 8, 20), 1, self._xml("43", "343")
                )
            ],
        ):
            action = document.action_import_from_mipyme()
        self.assertEqual(action["res_model"], "l10n_cl.dte.recovery")
        self.assertFalse(document.move_id)

    def test_active_historical_type_imports_directly(self):
        document_type = (
            self.env["l10n_latam.document.type"]
            .with_context(active_test=False)
            .search([("code", "=", "43"), ("country_id.code", "=", "CL")], limit=1)
        )
        document_type.active = True
        document = self._document("43", "344")
        with patch.object(
            ProductionMipymeAdapter,
            "retrieve",
            return_value=[
                PortalChunk(
                    date(2026, 8, 20), date(2026, 8, 20), 1, self._xml("43", "344")
                )
            ],
        ):
            action = document.action_import_from_mipyme()
        self.assertEqual(action["res_model"], "account.move")
        self.assertEqual(document.move_id.l10n_latam_document_type_id.code, "43")

    def test_export_foreign_currency_requires_fiscal_review(self):
        document = self._document("110", "310")
        with patch.object(
            ProductionMipymeAdapter,
            "retrieve",
            return_value=[
                PortalChunk(
                    date(2026, 8, 20),
                    date(2026, 8, 20),
                    1,
                    self._xml("110", "310", currency="DOLAR USA"),
                )
            ],
        ):
            action = document.action_import_from_mipyme()
        self.assertEqual(action["res_model"], "l10n_cl.dte.recovery")
        self.assertFalse(document.move_id)

    def test_export_foreign_currency_imports_with_matching_historic_rate(self):
        usd = self.env.ref("base.USD")
        usd.active = True
        company_total = usd._convert(
            100, self.company.currency_id, self.company, date(2026, 8, 20)
        )
        document = self._document(
            "110", "311", exempt_amount=company_total, total_amount=company_total
        )
        payload = self._xml("110", "311", currency="DOLAR USA").replace(
            b"</Totales>",
            (
                "</Totales><OtraMoneda><TpoMoneda>PESO CL</TpoMoneda>"
                f"<MntTotOtrMnda>{company_total}</MntTotOtrMnda></OtraMoneda>"
            ).encode(),
        )
        with patch.object(
            ProductionMipymeAdapter,
            "retrieve",
            return_value=[
                PortalChunk(date(2026, 8, 20), date(2026, 8, 20), 1, payload)
            ],
        ):
            action = document.action_import_from_mipyme()
        self.assertEqual(action["res_model"], "account.move")
        move = self.env["account.move"].browse(action["res_id"])
        self.assertEqual(move.currency_id, usd)
        self.assertEqual(move.partner_id.country_id.code, "US")
        self.assertEqual(document.reconciliation_state, "review")

    def test_export_folio_61_resolves_paraguay_customer_without_country_in_xml(self):
        paraguay = self.env.ref("base.py")
        customer = self._paraguay_customer()
        usd = self._set_folio_61_usd_rate()
        payload = self._folio_61_xml()
        document, action = self._import_folio_61(payload)
        self.assertEqual(action["res_model"], "account.move")
        move = self.env["account.move"].browse(action["res_id"])
        recovery = self.env["l10n_cl.dte.recovery"].search(
            [("dte_type", "=", "110"), ("folio", "=", 61)], limit=1
        )
        self.assertEqual(move.partner_id, customer)
        self.assertEqual(move.currency_id, usd)
        self.assertEqual(move.amount_total, 4080)
        self.assertEqual(abs(move.amount_total_signed), 3808558)
        self.assertEqual(move.state, "draft")
        self.assertEqual(recovery.recipient_country_id, paraguay)
        self.assertEqual(recovery.recipient_resolution_source, "existing_partner")
        self.assertEqual(recovery.recipient_resolution_digest, recovery.digest)
        self.assertEqual(recovery.attachment_id.raw, payload)
        self.assertEqual(
            self.env["res.partner"].search_count(
                [("vat", "=", "80122338-5"), ("country_id", "=", paraguay.id)]
            ),
            1,
        )
        with patch.object(ProductionMipymeAdapter, "retrieve") as retrieve:
            repeated_action = document.action_import_from_mipyme()
        retrieve.assert_not_called()
        self.assertEqual(repeated_action["res_id"], move.id)
        same_folio_moves = (
            self.env["account.move"]
            .search(
                [
                    ("company_id", "=", self.company.id),
                    (
                        "l10n_latam_document_type_id",
                        "=",
                        move.l10n_latam_document_type_id.id,
                    ),
                ]
            )
            .filtered(
                lambda invoice: invoice.l10n_latam_document_number.lstrip("0") == "61"
            )
        )
        self.assertEqual(len(same_folio_moves), 1)

    def test_duplicate_foreign_id_requires_operator_confirmation(self):
        paraguay_customer = self._paraguay_customer()
        second_customer = self.env["res.partner"].create(
            {
                "name": "Cliente Estados Unidos",
                "vat": "801223385",
                "country_id": self.env.ref("base.us").id,
                "company_type": "company",
                "l10n_cl_sii_taxpayer_type": "4",
            }
        )
        self._set_folio_61_usd_rate()
        document, action = self._import_folio_61(self._folio_61_xml())
        recovery = self.env["l10n_cl.dte.recovery"].browse(
            action.get("context", {}).get("default_recovery_id") or action.get("res_id")
        )
        self.assertEqual(
            action["res_model"],
            "l10n_cl.dte.recovery.wizard",
            (action, recovery.error_code, recovery.diagnostic),
        )
        self.assertEqual(recovery.error_code, "RECIPIENT_RESOLUTION_REQUIRED")
        self.assertFalse(recovery.move_id)
        wizard = (
            self.env["l10n_cl.dte.recovery.wizard"]
            .with_context(**action["context"])
            .create({})
        )
        self.assertTrue(wizard.needs_recipient_confirmation)
        self.assertEqual(
            wizard.candidate_partner_ids, paraguay_customer | second_customer
        )
        with self.assertRaises(AccessError):
            recovery.sudo().write({"recipient_partner_id": paraguay_customer.id})

        readonly_user = self.env["res.users"].create(
            {
                "name": "Sin permiso de recuperación",
                "login": "mipyme-recipient-readonly",
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            }
        )
        with self.assertRaises(AccessError):
            recovery.with_user(readonly_user).action_confirm_recipient(
                paraguay_customer.id, "Validación manual del cliente"
            )

        wizard.selected_partner_id = paraguay_customer
        wizard.resolution_reason = "El RUT y razón social coinciden con el contrato."
        wizard.action_confirm_recipient()
        self.assertEqual(recovery.recipient_partner_id, paraguay_customer)
        self.assertEqual(recovery.recipient_country_id.code, "PY")
        self.assertEqual(recovery.recipient_resolution_source, "operator")
        self.assertEqual(recovery.recipient_resolution_reason, wizard.resolution_reason)
        self.assertEqual(recovery.recipient_resolution_user_id, self.env.user)
        self.assertEqual(recovery.recipient_resolution_digest, recovery.digest)
        self.assertEqual(recovery.recipient_resolution_history[-1]["event"], "resolved")

        import_action = wizard.action_import_to_odoo()
        self.assertEqual(import_action["res_model"], "account.move")
        move = self.env["account.move"].browse(import_action["res_id"])
        self.assertEqual(move.partner_id, paraguay_customer)
        self.assertFalse(document.move_id)
        self.assertEqual(
            self.env["res.partner"].search_count(
                [("vat", "in", ["80122338-5", "801223385"])]
            ),
            2,
        )

    def test_changed_customer_invalidates_confirmation_and_keeps_review(self):
        customer = self._paraguay_customer()
        self._set_folio_61_usd_rate()
        payload = self._folio_61_xml(recipient_name="Razón social discordante")
        document, action = self._import_folio_61(payload)
        self.assertEqual(
            action["res_model"],
            "l10n_cl.dte.recovery.wizard",
            (
                action,
                document.reconciliation_state,
                self.env["l10n_cl.dte.recovery"]
                .search([("dte_type", "=", "110"), ("folio", "=", 61)], limit=1)
                .error_code,
                self.env["l10n_cl.dte.recovery"]
                .search([("dte_type", "=", "110"), ("folio", "=", 61)], limit=1)
                .diagnostic,
            ),
        )
        recovery = self.env["l10n_cl.dte.recovery"].browse(
            action["context"]["default_recovery_id"]
        )
        wizard = (
            self.env["l10n_cl.dte.recovery.wizard"]
            .with_context(**action["context"])
            .create({})
        )
        self.assertTrue(wizard.needs_recipient_confirmation)
        wizard.selected_partner_id = customer
        wizard.resolution_reason = "Confirmado contra antecedente comercial."
        wizard.action_confirm_recipient()
        customer.vat = "999999999"

        next_action = wizard.action_import_to_odoo()
        self.assertEqual(next_action["res_model"], "l10n_cl.dte.recovery.wizard")
        self.assertFalse(recovery.move_id)
        self.assertFalse(document.move_id)
        self.assertFalse(recovery.recipient_partner_id)
        self.assertEqual(recovery.status, "review")
        self.assertIn(
            "invalidated",
            [event["event"] for event in recovery.recipient_resolution_history],
        )

    def test_missing_foreign_id_cannot_be_confirmed_or_create_customer(self):
        customer = self._paraguay_customer()
        before = self.env["res.partner"].search_count([])
        payload = self._folio_61_xml().replace(b"<NumId>801223385</NumId>", b"")
        _document, action = self._import_folio_61(payload)
        self.assertEqual(
            action["res_model"],
            "l10n_cl.dte.recovery.wizard",
            (
                action,
                self.env["l10n_cl.dte.recovery"]
                .search([("dte_type", "=", "110"), ("folio", "=", 61)], limit=1)
                .error_code,
            ),
        )
        recovery = self.env["l10n_cl.dte.recovery"].browse(
            action["context"]["default_recovery_id"]
        )
        with self.assertRaises(UserError):
            recovery.action_confirm_recipient(customer.id, "Confirmación manual")
        self.assertEqual(self.env["res.partner"].search_count([]), before)
        self.assertFalse(recovery.move_id)

    def test_declared_country_cannot_be_overridden_by_confirmation(self):
        customer = self._paraguay_customer()
        self.company.partner_id.vat = "78275587-0"
        payload = self._folio_61_xml(folio="62", country_code="225")
        candidate = extract_candidates(payload)[0]
        recovery = self.env["l10n_cl.dte.recovery"].persist_result(
            RecoveryResult(
                "recovered",
                candidate.identity,
                candidate=candidate,
                digest=candidate.digest,
            ),
            self.company,
            date(2026, 9, 1),
            date(2026, 9, 30),
        )
        with self.assertRaises(UserError):
            recovery.action_confirm_recipient(customer.id, "El cliente es Paraguay")
        self.assertFalse(recovery.recipient_partner_id)
        self.assertFalse(recovery.move_id)

    def test_customer_from_another_company_cannot_be_confirmed(self):
        self.company.partner_id.vat = "78275587-0"
        other_company = self.setup_other_company(name="Otra compañía")
        other_customer = self.env["res.partner"].create(
            {
                "name": "Cliente restringido",
                "vat": "80122338-5",
                "country_id": self.env.ref("base.py").id,
                "company_id": other_company["company"].id,
                "company_type": "company",
                "l10n_cl_sii_taxpayer_type": "4",
            }
        )
        candidate = extract_candidates(self._folio_61_xml(folio="64"))[0]
        recovery = self.env["l10n_cl.dte.recovery"].persist_result(
            RecoveryResult(
                "recovered",
                candidate.identity,
                candidate=candidate,
                digest=candidate.digest,
            ),
            self.company,
            date(2026, 9, 1),
            date(2026, 9, 30),
        )
        with self.assertRaises(UserError):
            recovery.action_confirm_recipient(
                other_customer.id, "Cliente de otra compañía"
            )
        self.assertFalse(recovery.recipient_partner_id)
        self.assertFalse(recovery.move_id)

    def test_changed_xml_bytes_fail_digest_check_before_import(self):
        customer = self._paraguay_customer()
        self.company.partner_id.vat = "78275587-0"
        payload = self._folio_61_xml(folio="65")
        candidate = extract_candidates(payload)[0]
        recovery = self.env["l10n_cl.dte.recovery"].persist_result(
            RecoveryResult(
                "recovered",
                candidate.identity,
                candidate=candidate,
                digest=candidate.digest,
            ),
            self.company,
            date(2026, 9, 1),
            date(2026, 9, 30),
        )
        self.assertEqual(recovery.recipient_partner_id, customer)
        tampered_xml = payload.replace(
            b"<MntExe>4080</MntExe>", b"<MntExe>4081</MntExe>"
        )
        recovery.attachment_id.sudo().write({"datas": base64.b64encode(tampered_xml)})
        with self.assertRaises(UserError):
            recovery.action_import_to_odoo()
        self.assertFalse(recovery.move_id)

    def test_duplicate_export_invoices_remain_in_review(self):
        customer = self._paraguay_customer()
        self.company.partner_id.vat = "78275587-0"
        document_type = self.env.ref("l10n_cl.dc_fe_dte")
        usd = self.env.ref("base.USD")
        usd.active = True
        invoice_values = {
            "company_id": self.company.id,
            "partner_id": customer.id,
            "move_type": "out_invoice",
            "journal_id": self.sale_journal.id,
            "invoice_date": "2026-09-06",
            "currency_id": usd.id,
            "l10n_latam_document_type_id": document_type.id,
            "l10n_latam_document_number": "63",
            "invoice_line_ids": [
                (
                    0,
                    0,
                    {
                        "name": "Factura duplicada",
                        "account_id": self.sale_journal.default_account_id.id,
                        "price_unit": 4080,
                        "tax_ids": [(5, 0, 0)],
                    },
                )
            ],
        }
        first = self.env["account.move"].create(invoice_values)
        second = self.env["account.move"].create(invoice_values)
        payload = self._folio_61_xml(folio="63")
        candidate = extract_candidates(payload)[0]
        recovery = self.env["l10n_cl.dte.recovery"].persist_result(
            RecoveryResult(
                "recovered",
                candidate.identity,
                candidate=candidate,
                digest=candidate.digest,
            ),
            self.company,
            date(2026, 9, 1),
            date(2026, 9, 30),
        )
        self.assertEqual(recovery.status, "review")
        self.assertEqual(recovery.error_code, "INVOICE_AMBIGUOUS")
        self.assertFalse(recovery.move_id)
        self.assertEqual(first.state, "draft")
        self.assertEqual(second.state, "draft")

    def _native_export(self, folio="366"):
        self.sale_journal.l10n_cl_point_of_sale_type = "manual"
        customer = self.env["res.partner"].create(
            {
                "name": "Native exporter customer",
                "vat": "US-123456",
                "country_id": self.env.ref("base.us").id,
                "l10n_cl_sii_taxpayer_type": "4",
            }
        )
        usd = self.env.ref("base.USD")
        usd.active = True
        move = self.env["account.move"].create(
            {
                "company_id": self.company.id,
                "partner_id": customer.id,
                "move_type": "out_invoice",
                "journal_id": self.sale_journal.id,
                "invoice_date": "2026-08-20",
                "date": "2026-08-20",
                "currency_id": usd.id,
                "l10n_latam_document_type_id": self.env.ref("l10n_cl.dc_fe_dte").id,
                "l10n_latam_document_number": folio,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Native export",
                            "price_unit": 100,
                            "account_id": self.sale_journal.default_account_id.id,
                            "tax_ids": [(5, 0, 0)],
                        },
                    )
                ],
            }
        )
        move.action_post()
        document = self._document(
            "110",
            folio,
            exempt_amount=abs(move.amount_total_signed),
            total_amount=abs(move.amount_total_signed),
        )
        return move, document

    def test_native_export_without_recovery_and_real_differences(self):
        move, document = self._native_export()
        self.assertFalse(move.l10n_cl_mipyme_imported)
        document.action_reconcile()
        self.assertEqual(document.move_id, move)
        self.assertEqual(document.reconciliation_state, "matched")
        document.total_amount += 10
        document.action_reconcile()
        self.assertEqual(document.reconciliation_state, "amount_diff")
        document.total_amount -= 10
        document.tax_period = "202609"
        document.action_reconcile()
        self.assertEqual(document.reconciliation_state, "period_diff")

    def test_recovery_does_not_hide_native_duplicate(self):
        move, document = self._native_export()
        duplicate = move.copy({"l10n_latam_document_number": "366"})
        self.env["l10n_cl.dte.recovery"].create(
            {
                "company_id": self.company.id,
                "issuer_rut": "76543210-3",
                "dte_type": "110",
                "folio": 366,
                "status": "imported",
                "date_from": "2026-08-20",
                "date_to": "2026-08-20",
                "move_id": move.id,
            }
        )
        self.assertEqual(
            self.env["l10n.cl.rcv.matcher"]._candidate_moves(document), move | duplicate
        )
        document.action_reconcile()
        self.assertEqual(document.reconciliation_state, "duplicate")

    def test_xml_recipient_mismatch_never_creates_invoice(self):
        document = self._document()
        payload = self._xml(recipient="76543210-3")
        with patch.object(
            ProductionMipymeAdapter,
            "retrieve",
            return_value=[
                PortalChunk(date(2026, 8, 20), date(2026, 8, 20), 1, payload)
            ],
        ):
            action = document.action_import_from_mipyme()
        self.assertEqual(action["res_model"], "l10n_cl.dte.recovery")
        self.assertFalse(document.move_id)

    def test_rcv_amount_difference_keeps_invoice_for_review(self):
        document = self._document("33", "228", total_amount=120)
        with patch.object(
            ProductionMipymeAdapter,
            "retrieve",
            return_value=[
                PortalChunk(
                    date(2026, 8, 20), date(2026, 8, 20), 1, self._xml("33", "228")
                )
            ],
        ):
            action = document.action_import_from_mipyme()
        self.assertEqual(action["res_model"], "account.move")
        self.assertEqual(document.reconciliation_state, "amount_diff")
        self.assertEqual(document.move_id.id, action["res_id"])

    def test_portal_error_is_reviewable_without_accounting(self):
        document = self._document("34", "229")
        with patch.object(
            ProductionMipymeAdapter,
            "retrieve",
            side_effect=MipymeAdapterError(
                "RESPALDO_SIN_DATOS", "No hay DTE en el rango"
            ),
        ):
            action = document.action_import_from_mipyme()
        self.assertEqual(action["res_model"], "l10n_cl.dte.recovery")
        self.assertFalse(document.move_id)
        recovery = self.env["l10n_cl.dte.recovery"].browse(action["res_id"])
        self.assertEqual(recovery.status, "not_found")

    def test_same_folio_other_customer_is_not_associated(self):
        other = self.env["res.partner"].create(
            {"name": "Otro receptor", "vat": "76086428-5"}
        )
        document_type = self.env.ref("l10n_cl.dc_y_f_dte")
        wrong_move = self.env["account.move"].create(
            {
                "partner_id": other.id,
                "move_type": "out_invoice",
                "invoice_date": date(2026, 8, 20),
                "journal_id": self.sale_journal.id,
                "l10n_latam_document_type_id": document_type.id,
                "l10n_latam_document_number": "230",
                "company_id": self.company.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Venta distinta",
                            "account_id": self.sale_journal.default_account_id.id,
                            "price_unit": 100,
                        },
                    )
                ],
            }
        )
        document = self._document("34", "230")
        with patch.object(
            ProductionMipymeAdapter,
            "retrieve",
            return_value=[
                PortalChunk(
                    date(2026, 8, 20), date(2026, 8, 20), 1, self._xml("34", "230")
                )
            ],
        ):
            action = document.action_import_from_mipyme()
        recovery = self.env["l10n_cl.dte.recovery"].browse(action["res_id"])
        self.assertEqual(action["res_model"], "l10n_cl.dte.recovery")
        self.assertEqual(recovery.error_code, "INVOICE_RECIPIENT_MISMATCH")
        self.assertFalse(recovery.move_id)
        self.assertFalse(wrong_move.l10n_cl_dte_file)

    def test_operator_imports_without_general_recovery_write_access(self):
        operator = self.env["res.users"].create(
            {
                "name": "Operador RCV MiPyme",
                "login": "rcv-mipyme-operator",
                "company_id": self.company.id,
                "company_ids": [(6, 0, [self.company.id])],
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            self.env.ref("l10n_cl_rcv.group_rcv_manager").id,
                            self.env.ref(
                                "l10n_cl_dte_mipyme_recovery.group_mipyme_recovery_user"
                            ).id,
                        ],
                    )
                ],
            }
        )
        document = self._document("34", "227")
        payload = self._xml("34", "227")
        with patch.object(
            ProductionMipymeAdapter,
            "retrieve",
            return_value=[
                PortalChunk(date(2026, 8, 20), date(2026, 8, 20), 1, payload)
            ],
        ):
            action = (
                document.with_user(operator)
                .with_company(self.company)
                .action_import_from_mipyme()
            )
        self.assertEqual(action["res_model"], "account.move")
        recovery = self.env["l10n_cl.dte.recovery"].search(
            [("dte_type", "=", "34"), ("folio", "=", 227)], limit=1
        )
        self.assertEqual(recovery.move_id.id, action["res_id"])

    def test_server_rejects_missing_group_before_portal(self):
        document = self._document()
        user = self.env["res.users"].create(
            {
                "name": "Solo consulta RCV",
                "login": "rcv-readonly-one-click",
                "groups_id": [(6, 0, [self.env.ref("l10n_cl_rcv.group_rcv_user").id])],
            }
        )
        with patch.object(ProductionMipymeAdapter, "retrieve") as retrieve:
            with self.assertRaises(AccessError):
                document.with_user(user).action_import_from_mipyme()
        retrieve.assert_not_called()
