# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import base64
from unittest.mock import patch

from lxml import etree

from odoo.exceptions import UserError

from .common import RcvTestCommon


class TestRcvDraftWizardPartner(RcvTestCommon):
    def _make_document(self, issuer_vat="76123456-0"):
        return self.env["l10n.cl.rcv.document"].create(
            {
                "company_id": self.env.company.id,
                "operation": "purchase",
                "tax_period": "202609",
                "document_type_code": "33",
                "issuer_vat": issuer_vat,
                "issuer_name": "Proveedor RCV",
                "folio": "7123",
                "reconciliation_state": "missing",
            }
        )

    def _make_purchase_tax_with_rcv_alias(self, alias, name="RCV alias tax"):
        tag_model = self.env["account.account.tag"]
        country = self.env.ref("base.cl")

        def get_tag(name):
            tag = tag_model.search(
                [
                    ("name", "=", name),
                    ("applicability", "=", "taxes"),
                    ("country_id", "=", country.id),
                ],
                limit=1,
            )
            return tag or tag_model.create(
                {
                    "name": name,
                    "applicability": "taxes",
                    "country_id": country.id,
                }
            )

        invoice_tag = get_tag("+" + alias)
        refund_tag = get_tag("-" + alias)
        return self.env["account.tax"].create(
            {
                "name": name,
                "company_id": self.env.company.id,
                "tax_group_id": self.env["account.tax.group"].search([], limit=1).id,
                "l10n_cl_sii_code": 14,
                "type_tax_use": "purchase",
                "amount_type": "percent",
                "amount": 19,
                "invoice_repartition_line_ids": [
                    (0, 0, {"repartition_type": "base"}),
                    (
                        0,
                        0,
                        {
                            "repartition_type": "tax",
                            "tag_ids": [(6, 0, invoice_tag.ids)],
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
                            "tag_ids": [(6, 0, refund_tag.ids)],
                        },
                    ),
                ],
            }
        )

    def _deactivate_purchase_taxes(self):
        taxes = self.env["account.tax"].search(
            [
                ("company_id", "=", self.env.company.id),
                ("type_tax_use", "=", "purchase"),
                ("amount", ">", 0),
                ("active", "=", True),
            ]
        )
        taxes.write({"active": False})

    def test_analytic_widget_fields_are_readable_on_transient_line(self):
        wizard = self.env["l10n.cl.rcv.draft.wizard"].create(
            {"document_id": self._make_document().id}
        )
        line = self.env["l10n.cl.rcv.draft.wizard.line"].create(
            {"wizard_id": wizard.id, "name": "Línea analítica", "price_unit": 100}
        )

        self.assertIn("analytic_distribution", line._fields)
        self.assertIn("analytic_precision", line._fields)
        values = line.web_read({"analytic_distribution": {}, "analytic_precision": {}})

        self.assertEqual(len(values), 1)
        self.assertFalse(values[0]["analytic_distribution"])
        self.assertGreaterEqual(values[0]["analytic_precision"], 0)

    def test_unique_canonical_rut_partner_is_preselected(self):
        partner = self.env["res.partner"].create(
            {"name": "Proveedor existente", "vat": "CL 76.123.456-0"}
        )
        wizard = self.env["l10n.cl.rcv.draft.wizard"].create(
            {"document_id": self._make_document().id}
        )
        self.assertEqual(wizard.partner_candidate_ids, partner)
        self.assertEqual(wizard.partner_id, partner)
        self.assertFalse(wizard.new_partner_name)

    def test_missing_partner_is_proposed_then_created_with_canonical_rut(self):
        wizard = self.env["l10n.cl.rcv.draft.wizard"].create(
            {"document_id": self._make_document("CL 76.123.456-0").id}
        )
        self.assertFalse(wizard.partner_id)
        self.assertEqual(wizard.new_partner_name, "Proveedor RCV")
        self.assertFalse(wizard.partner_candidate_ids)
        self.assertFalse(
            self.env["res.partner"].search(
                [("vat", "=", "76123456-0"), ("name", "=", "Proveedor RCV")]
            )
        )

        partner = wizard._resolve_or_create_partner()

        self.assertEqual(partner.name, "Proveedor RCV")
        self.assertEqual(partner.vat, "76123456-0")
        self.assertEqual(partner.supplier_rank, 1)

    def test_duplicate_canonical_rut_requires_existing_partner_selection(self):
        first = self.env["res.partner"].create(
            {"name": "Proveedor uno", "vat": "76123456-0"}
        )
        second = self.env["res.partner"].create(
            {"name": "Proveedor dos", "vat": "CL 76.123.456-0"}
        )
        wizard = self.env["l10n.cl.rcv.draft.wizard"].create(
            {"document_id": self._make_document().id}
        )
        self.assertEqual(wizard.partner_candidate_ids, first | second)
        self.assertFalse(wizard.partner_id)
        with self.assertRaises(UserError):
            wizard._resolve_or_create_partner()

        wizard.partner_id = second
        self.assertEqual(wizard._resolve_or_create_partner(), second)

    def test_unique_company_partner_wins_over_same_rut_contacts(self):
        company = self.env["res.partner"].create(
            {
                "name": "Proveedor compañía",
                "vat": "76123456-0",
                "company_type": "company",
            }
        )
        contact = self.env["res.partner"].create(
            {
                "name": "Contacto proveedor",
                "vat": "CL 76.123.456-0",
                "parent_id": company.id,
            }
        )

        wizard = self.env["l10n.cl.rcv.draft.wizard"].create(
            {"document_id": self._make_document().id}
        )

        self.assertTrue(contact)
        self.assertEqual(wizard.partner_candidate_ids, company)
        self.assertEqual(wizard.partner_id, company)
        self.assertEqual(wizard._resolve_or_create_partner(), company)

    def test_multiple_company_partners_with_same_rut_stay_ambiguous(self):
        companies = self.env["res.partner"].create(
            [
                {
                    "name": "Proveedor compañía uno",
                    "vat": "76123456-0",
                    "company_type": "company",
                },
                {
                    "name": "Proveedor compañía dos",
                    "vat": "CL 76.123.456-0",
                    "company_type": "company",
                },
            ]
        )
        wizard = self.env["l10n.cl.rcv.draft.wizard"].create(
            {"document_id": self._make_document().id}
        )

        self.assertEqual(wizard.partner_candidate_ids, companies)
        self.assertFalse(wizard.partner_id)
        with self.assertRaises(UserError):
            wizard._resolve_or_create_partner()

    def test_default_purchase_account_normalizes_integer_and_record_results(self):
        partner = self.env["res.partner"].create(
            {"name": "Proveedor cuenta RCV", "vat": "76123456-0"}
        )
        account = self.env["account.account"].create(
            {
                "name": "RCV suggested expense",
                "code": "RCVSUG",
                "account_type": "expense",
                "company_ids": [(6, 0, self.env.company.ids)],
            }
        )
        wizard = self.env["l10n.cl.rcv.draft.wizard"].create(
            {"document_id": self._make_document().id, "partner_id": partner.id}
        )
        account_model = type(self.env["account.account"])

        with patch.object(
            account_model,
            "_get_most_frequent_account_for_partner",
            return_value=account.id,
        ):
            self.assertEqual(wizard._default_purchase_account(partner), account)

        with patch.object(
            account_model,
            "_get_most_frequent_account_for_partner",
            return_value=account,
        ):
            self.assertEqual(wizard._default_purchase_account(partner), account)

    def test_invalid_default_account_is_not_returned(self):
        partner = self.env["res.partner"].create(
            {"name": "Proveedor sin cuenta", "vat": "76123456-0"}
        )
        wizard = self.env["l10n.cl.rcv.draft.wizard"].create(
            {"document_id": self._make_document().id, "partner_id": partner.id}
        )
        wizard.journal_id.default_account_id = False

        with patch.object(
            type(self.env["account.account"]),
            "_get_most_frequent_account_for_partner",
            return_value=999999999,
        ):
            self.assertFalse(wizard._default_purchase_account(partner))

    def test_autocomplete_without_xml_requires_tax_review(self):
        self._deactivate_purchase_taxes()
        partner = self.env["res.partner"].create(
            {"name": "Proveedor RCV", "vat": "76123456-0"}
        )
        document = self._make_document()
        document.write({"net_amount": 100, "vat_amount": 19, "total_amount": 119})
        wizard = self.env["l10n.cl.rcv.draft.wizard"].create(
            {"document_id": document.id, "partner_id": partner.id}
        )

        self.assertTrue(wizard.action_autocomplete_from_rcv())
        self.assertEqual(len(wizard.line_ids), 1)
        self.assertEqual(wizard.line_ids.price_unit, 100)
        self.assertEqual(wizard.line_ids.tax_treatment, "unresolved")
        self.assertEqual(wizard.rcv_net_amount, 100)
        self.assertEqual(wizard.rcv_vat_amount, 19)
        self.assertEqual(wizard.draft_untaxed_amount, 100)
        with self.assertRaises(UserError):
            wizard.action_create_draft()

    def test_ambiguous_tax_tag_does_not_select_a_tax(self):
        self._deactivate_purchase_taxes()
        tag = self.env.ref("l10n_cl.tag_cl_purchase_mnt_iva")
        taxes = self.env["account.tax"]
        for name in ("RCV IVA test A", "RCV IVA test B"):
            taxes.create(
                {
                    "name": name,
                    "company_id": self.env.company.id,
                    "type_tax_use": "purchase",
                    "amount_type": "percent",
                    "amount": 19,
                    "invoice_repartition_line_ids": [
                        (0, 0, {"repartition_type": "base"}),
                        (
                            0,
                            0,
                            {
                                "repartition_type": "tax",
                                "tag_ids": [(6, 0, tag.ids)],
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
                                "tag_ids": [(6, 0, tag.ids)],
                            },
                        ),
                    ],
                }
            )
        wizard = self.env["l10n.cl.rcv.draft.wizard"].create(
            {"document_id": self._make_document().id}
        )

        self.assertFalse(
            wizard._find_rcv_component_tax("l10n_cl.tag_cl_purchase_mnt_iva")
        )

    def test_unique_chile_chart_alias_resolves_to_one_purchase_tax(self):
        self._deactivate_purchase_taxes()
        expected = self._make_purchase_tax_with_rcv_alias(
            "IVA Pagado Compras Recuperables"
        )
        wizard = self.env["l10n.cl.rcv.draft.wizard"].create(
            {"document_id": self._make_document().id}
        )

        self.assertEqual(
            wizard._find_rcv_component_tax("l10n_cl.tag_cl_purchase_mnt_iva"),
            expected,
        )

    def test_ambiguous_chile_chart_alias_does_not_select_a_tax(self):
        self._deactivate_purchase_taxes()
        self._make_purchase_tax_with_rcv_alias("IVA Pagado Compras Recuperables")
        self._make_purchase_tax_with_rcv_alias(
            "IVA Pagado Compras Recuperables", "RCV alias duplicate"
        )
        wizard = self.env["l10n.cl.rcv.draft.wizard"].create(
            {"document_id": self._make_document().id}
        )

        self.assertFalse(
            wizard._find_rcv_component_tax("l10n_cl.tag_cl_purchase_mnt_iva")
        )

    def test_exempt_amount_autocompletes_without_inventing_tax(self):
        document = self._make_document()
        document.write({"exempt_amount": 50, "total_amount": 50})
        wizard = self.env["l10n.cl.rcv.draft.wizard"].create(
            {"document_id": document.id}
        )

        wizard.action_autocomplete_from_rcv()

        self.assertEqual(len(wizard.line_ids), 1)
        self.assertEqual(wizard.line_ids.price_unit, 50)
        self.assertFalse(wizard.line_ids.tax_ids)
        self.assertEqual(wizard.line_ids.tax_treatment, "exempt")

    def test_interactive_autocomplete_stays_limited_to_missing_rcv_rows(self):
        document = self._make_document()
        document.reconciliation_state = "review"
        wizard = self.env["l10n.cl.rcv.draft.wizard"].create(
            {"document_id": document.id}
        )

        with self.assertRaises(UserError):
            wizard.action_autocomplete_from_rcv()

    def test_invalid_xml_does_not_create_supplier(self):
        document = self._make_document("76876543-2")
        wizard = self.env["l10n.cl.rcv.draft.wizard"].create(
            {
                "document_id": document.id,
                "xml_file": base64.b64encode(b"<not-xml"),
            }
        )

        with self.assertRaises(UserError):
            wizard.action_create_draft()

        self.assertFalse(self.env["res.partner"].search([("vat", "=", "76876543-2")]))

    def test_valid_xml_identity_accepts_canonical_rut_variant(self):
        wizard = self.env["l10n.cl.rcv.draft.wizard"].create(
            {"document_id": self._make_document().id}
        )
        dte = etree.fromstring(
            b'<DTE xmlns="http://www.sii.cl/SiiDte"><Documento>'
            b"<Encabezado><IdDoc><TipoDTE>33</TipoDTE><Folio>7123</Folio></IdDoc>"
            b"<Emisor><RUTEmisor>76.123.456-0</RUTEmisor></Emisor></Encabezado>"
            b"</Documento></DTE>"
        )
        self.assertTrue(wizard._validate_xml_identity(dte))
