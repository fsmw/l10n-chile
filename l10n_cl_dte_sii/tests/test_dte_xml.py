# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
import re
from datetime import date
from difflib import unified_diff
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from lxml import etree

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from ..services.dte_xml import (
    DTE_NS,
    DteXmlBuilder,
    _caf_nodes,
    normalize_rut,
    validate_dte_envelope,
    validate_dte_xml,
)


@tagged("post_install", "-at_install")
class TestDteXmlBuilder(AccountTestInvoicingCommon):
    @classmethod
    @AccountTestInvoicingCommon.setup_country("cl")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.env.user.write(
            {"groups_id": [(4, cls.env.ref("l10n_cl_dte_sii.group_dte_manager").id)]}
        )
        cls.company.partner_id.vat = "76543210-3"
        cls.company.l10n_cl_dte_activity = "Servicios de prueba"
        cls.company.l10n_cl_dte_activity_code = "620200"
        cls.company.l10n_cl_dte_resolution_number = "0"
        cls.company.l10n_cl_dte_resolution_date = date(2020, 1, 1)
        cls.partner_a.country_id = cls.company.country_id
        cls.partner_a.vat = "60803000-K"
        cls.partner_a.l10n_cl_sii_taxpayer_type = "1"
        cls.document_type = cls.env["l10n_latam.document.type"].search(
            [("country_id.code", "=", "CL"), ("code", "=", "33")], limit=1
        )

    def _caf(self, document_code="33", start=100, end=110):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")
        numbers = key.public_key().public_numbers()
        modulus = base64.b64encode(
            numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
        ).decode("ascii")
        exponent = base64.b64encode(
            numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
        ).decode("ascii")
        caf_xml = (
            "<AUTORIZACION><CAF version='1.0'><DA>"
            f"<RE>{self.company.vat}</RE><RS>Emisor de prueba</RS>"
            f"<TD>{document_code}</TD><RNG><D>{start}</D><H>{end}</H></RNG>"
            f"<FA>2020-01-01</FA><RSAPK><M>{modulus}</M><E>{exponent}</E></RSAPK>"
            "<IDK>100</IDK></DA><FRMA algoritmo='SHA1withRSA'>AA==</FRMA>"
            f"<RSASK>{escape(pem)}</RSASK>"
            "</CAF></AUTORIZACION>"
        ).encode()
        caf = self.env["l10n_cl.dte.caf"].create_from_xml(
            "synthetic-caf.xml", caf_xml, self.company
        )
        caf.action_enable()
        return caf._decode_caf(), key

    def _certificate(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = key.public_key().public_numbers()

        class SyntheticCertificate:
            subject_serial_number = "76543210-3"

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

        return SyntheticCertificate(), key

    def test_caf_private_key_outside_caf_element_is_accepted(self):
        """El CAF auténtico del SII trae RSASK como hermano de CAF."""
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ).decode("ascii")
        payload = (
            "<AUTORIZACION><CAF version='1.0'><DA>"
            f"<RE>{self.company.vat}</RE><RS>Emisor</RS><TD>33</TD>"
            "<RNG><D>1</D><H>10</H></RNG><FA>2020-01-01</FA>"
            "<RSAPK><M>AA==</M><E>AQAB</E></RSAPK><IDK>100</IDK>"
            "</DA><FRMA algoritmo='SHA1withRSA'>AA==</FRMA></CAF>"
            f"<RSASK>{escape(pem)}</RSASK></AUTORIZACION>"
        )
        _caf, _da, _frma, rsask = _caf_nodes(etree.fromstring(payload.encode()))
        self.assertIn("BEGIN RSA PRIVATE KEY", rsask)

    def test_ted_signature_covers_indented_caf_without_layout_whitespace(self):
        """El CAF del SII trae sangría: el TED firmado debe quedar compacto."""
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ).decode("ascii")
        numbers = key.public_key().public_numbers()
        modulus = base64.b64encode(
            numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
        ).decode("ascii")
        exponent = base64.b64encode(
            numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
        ).decode("ascii")
        caf_xml = (
            "<AUTORIZACION>\n"
            "  <CAF version='1.0'>\n"
            "    <DA>\n"
            f"      <RE>{self.company.vat}</RE>\n"
            "      <RS>Emisor de prueba</RS>\n"
            "      <TD>33</TD>\n"
            "      <RNG>\n"
            "        <D>500</D>\n"
            "        <H>509</H>\n"
            "      </RNG>\n"
            "      <FA>2020-01-01</FA>\n"
            f"      <RSAPK><M>{modulus}</M><E>{exponent}</E></RSAPK>\n"
            "      <IDK>100</IDK>\n"
            "    </DA>\n"
            "    <FRMA algoritmo='SHA1withRSA'>AA==</FRMA>\n"
            "  </CAF>\n"
            f"  <RSASK>{escape(pem)}</RSASK>\n"
            "</AUTORIZACION>\n"
        ).encode()
        caf = self.env["l10n_cl.dte.caf"].create_from_xml(
            "indented-caf.xml", caf_xml, self.company
        )
        caf.action_enable()
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": self.company_data["default_journal_sale"].id,
                "partner_id": self.partner_a.id,
                "invoice_date": "2026-10-28",
                "l10n_latam_document_type_id": self.document_type.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Servicio con CAF indentado",
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
        certificate, _certificate_key = self._certificate()
        dte = DteXmlBuilder(invoice, 500, caf._decode_caf(), certificate).build()
        document = dte.find(f".//{{{DTE_NS}}}Documento")
        dd = document.find(f"{{{DTE_NS}}}TED/{{{DTE_NS}}}DD")
        for element in dd.iter():
            self.assertFalse(
                element.text is not None and not element.text.strip(),
                "El TED emitido conserva sangría del CAF.",
            )
            self.assertFalse(
                element.tail is not None and not element.tail.strip(),
                "El TED emitido conserva sangría del CAF.",
            )
        payload = re.sub(
            rb"\n\s*",
            b"",
            re.search(
                rb"<DD\b[^>]*>.*?</DD>",
                etree.tostring(dte, encoding="ISO-8859-1"),
                re.S,
            ).group(0),
        )
        frmt = base64.b64decode(document.findtext(f"{{{DTE_NS}}}TED/{{{DTE_NS}}}FRMT"))
        key.public_key().verify(frmt, payload, padding.PKCS1v15(), hashes.SHA1())

    def test_normalize_rut_removes_chilean_country_prefix(self):
        self.assertEqual(normalize_rut("CL762012243"), "76201224-3")
        self.assertEqual(normalize_rut("CL76.201.224-3"), "76201224-3")
        self.assertEqual(normalize_rut("76.201.224-3"), "76201224-3")

    def test_invalid_dte_and_submission_envelopes_fail_xsd_validation(self):
        invalid_dte = etree.Element(f"{{{DTE_NS}}}DTE")
        invalid_envelope = etree.Element(f"{{{DTE_NS}}}EnvioDTE")
        invalid_boleta_envelope = etree.Element(f"{{{DTE_NS}}}EnvioBOLETA")

        with self.assertRaises(ValidationError):
            validate_dte_xml(invalid_dte, "33", context=self.env.context)
        with self.assertRaises(ValidationError):
            validate_dte_envelope(invalid_envelope, "33", context=self.env.context)
        with self.assertRaises(ValidationError):
            validate_dte_envelope(
                invalid_boleta_envelope, "39", context=self.env.context
            )

    def test_basic_invoice_payload_matches_local_golden(self):
        golden = Path(__file__).resolve().parent / "expected_dtes" / "dte_33.xml"
        self.assertTrue(golden.is_file(), f"Missing DTE XML fixture: {golden}")

        company_partner = self.company.partner_id
        company_partner.write(
            {
                "name": "Blanco Martin & Asociados EIRL",
                "street": "Apoquindo 6410",
                "city": "Les Condes",
                "phone": "+1 (650) 691-3277 ",
                "vat": "CL762012243",
            }
        )
        self.company.write(
            {
                "l10n_cl_dte_activity": "activity_test",
                "l10n_cl_dte_activity_code": "620200",
                "l10n_cl_dte_email": "info@bmya.cl",
            }
        )
        self.partner_a.write(
            {
                "name": "Partner SII",
                "vat": "76086428-5",
                "street": "Puerto Test 102",
                "city": "Pudahuel",
                "phone": "+562 0000 0000",
                "email": "partner@sii.cl",
                "l10n_cl_dte_email": "partner@sii.cl",
                "l10n_cl_activity_description": "activity_test",
                "l10n_cl_sii_taxpayer_type": "1",
            }
        )
        journal = self.company_data["default_journal_sale"]
        tax = self.env["account.tax"].search(
            [
                ("name", "=", "19% VAT"),
                ("type_tax_use", "=", "sale"),
                ("company_id", "=", self.company.id),
            ],
            limit=1,
        )
        self.assertTrue(tax)
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": journal.id,
                "partner_id": self.partner_a.id,
                "currency_id": self.env.ref("base.CLP").id,
                "invoice_date": "2019-10-23",
                "invoice_date_due": "2019-10-23",
                "l10n_latam_document_type_id": self.document_type.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": 'Tapa Ranurada UL FM 300 6"',
                            "product_id": self.product_a.id,
                            "product_uom_id": self.product_a.uom_id.id,
                            "quantity": 26,
                            "price_unit": 2391,
                            "account_id": self.company_data[
                                "default_account_revenue"
                            ].id,
                            "tax_ids": [(6, 0, tax.ids)],
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "name": 'Copla Flexible 1NS 6"',
                            "product_id": self.product_a.id,
                            "product_uom_id": self.product_a.uom_id.id,
                            "quantity": 80,
                            "price_unit": 2914,
                            "account_id": self.company_data[
                                "default_account_revenue"
                            ].id,
                            "tax_ids": [(6, 0, tax.ids)],
                        },
                    ),
                ],
            }
        )
        caf_root, _caf_key = self._caf("33", start=1)
        certificate, _certificate_key = self._certificate()
        builder = DteXmlBuilder(invoice, 1, caf_root, certificate)
        actual = builder.build()
        builder.build_envelope(actual)
        expected = etree.parse(
            str(golden), parser=etree.XMLParser(remove_blank_text=True)
        ).getroot()
        self._assert_golden_document(actual, expected)

    def test_remaining_payloads_match_local_goldens(self):
        fixture_dir = Path(__file__).resolve().parent / "expected_dtes"
        names = (
            "dte_33_with_discounts.xml",
            "dte_33_usd_with_discounts.xml",
            "dte_33_with_holding_taxes.xml",
            "dte_33_with_reference_ids.xml",
            "dte_34.xml",
            "dte_39.xml",
            "dte_56.xml",
        )
        missing_fixtures = [
            name for name in names if not (fixture_dir / name).is_file()
        ]
        self.assertFalse(
            missing_fixtures, f"Missing DTE XML fixtures: {missing_fixtures}"
        )

        self._configure_golden_issuer()
        scenarios = [
            {
                "filename": names[0],
                "code": "33",
                "lines": [
                    ('Tapa Ranurada UL FM 300 6"', 200, 5, 5.99, ("19% VAT",)),
                    ('Copla Flexible 1NS 6"', 300, 800, 9.77, ("19% VAT",)),
                    ("Desk Combination", 5, 40000, 10, ("19% VAT",)),
                ],
            },
            {
                "filename": names[1],
                "code": "33",
                "date": "2022-11-24",
                "currency": "base.USD",
                "usd_rate": 0.001069187097,
                "lines": [
                    ('Tapa Ranurada UL FM 300 6"', 2, 123.45, 10, ("19% VAT",)),
                    ('Copla Flexible 1NS 6"', 1, 12.31, 5, ("19% VAT",)),
                ],
            },
            {
                "filename": names[2],
                "code": "33",
                "lines": [
                    (
                        "FALERNIA CABERNET SAUVIGNON RESERVA 2018 750ML 14",
                        1,
                        31110,
                        0,
                        ("19% VAT", "20.5% ILA"),
                    ),
                    (
                        "FALERNIA CARMENERE 2017 RESERVA 14 750ML",
                        1,
                        31110,
                        0,
                        ("19% VAT", "20.5% ILA"),
                    ),
                    ("FAL CARMENERE 2017 375CC", 1, 36210, 0, ("19% VAT", "20.5% ILA")),
                    (
                        "FALERNIA CARMENERE GRAN RESERVA 2016 750ML 15",
                        1,
                        26138,
                        0,
                        ("19% VAT", "20.5% ILA"),
                    ),
                    (
                        "FALERNIA C. SAUVIGNON GR.RESERVA 2017 750ML 14,5",
                        1,
                        26138,
                        0,
                        ("19% VAT", "20.5% ILA"),
                    ),
                    ("COCA COLA NO RETORNABLE 2L", 12, 800, 0, ("19% VAT", "10% ILA")),
                    ("COSTO LOGISTICO", 1, 7143, 0, ("19% VAT",)),
                ],
            },
            {
                "filename": names[3],
                "code": "33",
                "lines": self._basic_golden_lines(),
                "references": [
                    ("l10n_cl.dc_odc", "PO00273", "2019-10-18", "", "Test"),
                    ("l10n_cl.dc_gd_dte", "996327", "2019-10-18", "", "Test"),
                ],
            },
            {
                "filename": names[4],
                "code": "34",
                "date": "2019-10-22",
                "product_name": "Desk Combination",
                "product_code": "FURN_7800",
                "lines": [
                    ("Desk Combination", 1, 1200000, 0, ()),
                    ("Desk Combination", 1, 2400000, 0, ()),
                ],
            },
            {
                "filename": names[5],
                "code": "39",
                "anonymous": True,
                "lines": [
                    ("Regla De Anchura De Grietas", 1, 10694.7, 0, ("19% VAT",)),
                    ("Despacho Chilexpress", 1, 6000, 0, ("19% VAT",)),
                ],
            },
            {
                "filename": names[6],
                "code": "56",
                "lines": [
                    (
                        "[WUS-0558538] Conjunto De Control Electrónico Ps3.39",
                        1,
                        497804.44,
                        10,
                        ("19% VAT",),
                    ),
                    ("[WUS-0558424A] Vastago Ps 3.39", 1, 171375.56, 10, ("19% VAT",)),
                    ("[WUS-0555217] Filtro Malla Acero", 1, 6801.11, 10, ("19% VAT",)),
                    (
                        "[WUS-0524421] Retenedor Wagner 339",
                        1,
                        19722.22,
                        10,
                        ("19% VAT",),
                    ),
                    ("[INSUMO] Insumos Varios", 2, 4760, 0, ("19% VAT",)),
                    (
                        "[SERVICIO03] Servicio de Reparación Equipo Airless "
                        "(Fieldlazer, 695-MarkV, PS3.29-3.39)",
                        1,
                        114250,
                        0,
                        ("19% VAT",),
                    ),
                    (
                        "[WUS-0558587] Kit Reparación Ps3 .39",
                        1,
                        150973.33,
                        10,
                        ("19% VAT",),
                    ),
                ],
                "references": [
                    (
                        "l10n_cl.dc_nc_f_dte",
                        "1961",
                        "2019-10-23",
                        "1",
                        "Anulación NC por aceptación con reparo (N/C 001961)",
                    ),
                ],
            },
        ]
        for scenario in scenarios:
            with self.subTest(golden=scenario["filename"]):
                self._configure_golden_issuer()
                if scenario.get("product_name"):
                    self.product_a.write(
                        {
                            "name": scenario["product_name"],
                            "default_code": scenario.get("product_code"),
                        }
                    )
                else:
                    self.product_a.write({"name": "product_a", "default_code": False})
                partner = self.partner_a
                if scenario.get("anonymous"):
                    partner.write(
                        {
                            "name": "Consumidor Final Anonimo",
                            "vat": "66666666-6",
                            "l10n_cl_sii_taxpayer_type": "3",
                            "street": False,
                            "street2": False,
                            "city": "N-A",
                            "phone": False,
                        }
                    )
                tax_by_name = {
                    name: self.env["account.tax"].search(
                        [
                            ("name", "=", name),
                            ("type_tax_use", "=", "sale"),
                            ("company_id", "=", self.company.id),
                        ],
                        limit=1,
                    )
                    for line in scenario["lines"]
                    for name in line[4]
                }
                self.assertTrue(all(tax_by_name.values()))
                invoice_date = scenario.get("date", "2019-10-23")
                currency = self.env.ref(scenario.get("currency", "base.CLP"))
                if scenario.get("usd_rate"):
                    currency.active = True
                    self.env["res.currency.rate"].create(
                        {
                            "name": invoice_date,
                            "company_id": self.company.id,
                            "currency_id": currency.id,
                            "rate": scenario["usd_rate"],
                        }
                    )
                document_type = self.env["l10n_latam.document.type"].search(
                    [
                        ("country_id.code", "=", "CL"),
                        ("code", "=", scenario["code"]),
                    ],
                    limit=1,
                )
                lines = []
                for name, quantity, price, discount, tax_names in scenario["lines"]:
                    lines.append(
                        (
                            0,
                            0,
                            {
                                "name": name,
                                "product_id": self.product_a.id,
                                "product_uom_id": self.product_a.uom_id.id,
                                "quantity": quantity,
                                "price_unit": price,
                                "discount": discount,
                                "account_id": self.company_data[
                                    "default_account_revenue"
                                ].id,
                                "tax_ids": [
                                    (6, 0, [tax_by_name[t].id for t in tax_names])
                                ],
                            },
                        )
                    )
                invoice = self.env["account.move"].create(
                    {
                        "move_type": "out_invoice",
                        "company_id": self.company.id,
                        "journal_id": self.company_data["default_journal_sale"].id,
                        "partner_id": partner.id,
                        "currency_id": currency.id,
                        "invoice_date": invoice_date,
                        "invoice_date_due": invoice_date,
                        "l10n_latam_document_type_id": document_type.id,
                        "invoice_line_ids": lines,
                    }
                )
                for xmlid, number, ref_date, ref_code, reason in scenario.get(
                    "references", []
                ):
                    invoice.write(
                        {
                            "l10n_cl_reference_ids": [
                                (
                                    0,
                                    0,
                                    {
                                        "l10n_cl_reference_doc_type_id": self.env.ref(
                                            xmlid
                                        ).id,
                                        "origin_doc_number": number,
                                        "date": ref_date,
                                        "reference_doc_code": ref_code,
                                        "reason": reason,
                                    },
                                )
                            ]
                        }
                    )
                document_type_cafs = self.env["l10n_cl.dte.caf"].search(
                    [
                        ("company_id", "=", self.company.id),
                        ("document_type_id", "=", document_type.id),
                        ("state", "=", "in_use"),
                    ]
                )
                document_type_cafs.action_disable()
                expected = etree.parse(
                    str(fixture_dir / scenario["filename"]),
                    parser=etree.XMLParser(remove_blank_text=True),
                ).getroot()
                folio = int(expected.findtext(f".//{{{DTE_NS}}}Folio"))
                caf_root, _caf_key = self._caf(
                    scenario["code"], start=folio, end=folio + 9
                )
                certificate, _certificate_key = self._certificate()
                builder = DteXmlBuilder(invoice, folio, caf_root, certificate)
                actual = builder.build()
                builder.build_envelope(actual)
                self._assert_golden_document(actual, expected)

    def test_exempt_receipt_41_builds_valid_signed_submission_envelope(self):
        self._configure_golden_issuer()
        document_type = self.env["l10n_latam.document.type"].search(
            [("country_id.code", "=", "CL"), ("code", "=", "41")], limit=1
        )
        self.assertTrue(document_type)
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": self.company_data["default_journal_sale"].id,
                "partner_id": self.partner_a.id,
                "currency_id": self.env.ref("base.CLP").id,
                "invoice_date": "2026-10-28",
                "l10n_latam_document_type_id": document_type.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Servicio exento de prueba",
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
        caf_root, _caf_key = self._caf("41", start=1)
        certificate, _certificate_key = self._certificate()
        builder = DteXmlBuilder(invoice, 1, caf_root, certificate)
        signed_dte = builder.build()
        envelope = builder.build_envelope(signed_dte)

        self.assertEqual(envelope.tag.rsplit("}", 1)[-1], "EnvioBOLETA")
        self.assertEqual(envelope.findtext(f".//{{{DTE_NS}}}TpoDTE"), "41")

    def test_credit_note_61_builds_valid_signed_submission_envelope(self):
        self._configure_golden_issuer()
        self.company_data["default_journal_sale"].write(
            {"l10n_latam_use_documents": True}
        )
        source_type = self.env["l10n_latam.document.type"].search(
            [("country_id.code", "=", "CL"), ("code", "=", "33")], limit=1
        )
        source = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": self.company_data["default_journal_sale"].id,
                "partner_id": self.partner_a.id,
                "currency_id": self.env.ref("base.CLP").id,
                "invoice_date": "2026-10-28",
                "l10n_latam_document_type_id": source_type.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Servicio de prueba",
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
        source.with_context(l10n_cl_skip_dte_emission=True).action_post()
        wizard = (
            self.env["account.move.reversal"]
            .with_context(active_model="account.move", active_ids=source.ids)
            .create(
                {
                    "reason": "Anulación por error de monto",
                    "journal_id": self.company_data["default_journal_sale"].id,
                }
            )
        )
        action = wizard.refund_moves()
        credit_note = self.env["account.move"].browse(action["res_id"])
        self.assertEqual(credit_note.l10n_latam_document_type_id.code, "61")
        self.assertTrue(credit_note.l10n_cl_reference_ids)

        caf_root, _caf_key = self._caf("61", start=1)
        certificate, _certificate_key = self._certificate()
        builder = DteXmlBuilder(credit_note, 1, caf_root, certificate)
        signed_dte = builder.build()
        envelope = builder.build_envelope(signed_dte)

        self.assertEqual(envelope.tag.rsplit("}", 1)[-1], "EnvioDTE")
        self.assertEqual(envelope.findtext(f".//{{{DTE_NS}}}TpoDTE"), "61")

    def _configure_golden_issuer(self):
        self.company.partner_id.write(
            {
                "name": "Blanco Martin & Asociados EIRL",
                "street": "Apoquindo 6410",
                "city": "Les Condes",
                "phone": "+1 (650) 691-3277 ",
                "vat": "CL762012243",
            }
        )
        self.company.write(
            {
                "l10n_cl_dte_activity": "activity_test",
                "l10n_cl_dte_activity_code": "620200",
                "l10n_cl_dte_email": "info@bmya.cl",
                "tax_calculation_rounding_method": "round_globally",
            }
        )
        self.partner_a.write(
            {
                "name": "Partner SII",
                "vat": "76086428-5",
                "street": "Puerto Test 102",
                "city": "Pudahuel",
                "phone": "+562 0000 0000",
                "email": "partner@sii.cl",
                "l10n_cl_dte_email": "partner@sii.cl",
                "l10n_cl_activity_description": "activity_test",
                "l10n_cl_sii_taxpayer_type": "1",
            }
        )

    def _basic_golden_lines(self):
        return [
            ('Tapa Ranurada UL FM 300 6"', 26, 2391, 0, ("19% VAT",)),
            ('Copla Flexible 1NS 6"', 80, 2914, 0, ("19% VAT",)),
        ]

    def _assert_golden_document(self, actual, expected):
        actual_document = actual.find(f".//{{{DTE_NS}}}Documento")
        expected_document = expected.find(f".//{{{DTE_NS}}}Documento")
        self.assertIsNotNone(actual_document)
        self.assertIsNotNone(expected_document)
        for document in (actual_document, expected_document):
            for name in ("TED", "TmstFirma"):
                node = document.find(f"{{{DTE_NS}}}{name}")
                if node is not None:
                    document.remove(node)
            # The seller name in this optional header field depends on test user.
            seller_name_nodes = document.findall(
                f"{{{DTE_NS}}}Encabezado/{{{DTE_NS}}}Emisor/{{{DTE_NS}}}CdgVendedor"
            )
            for node in seller_name_nodes:
                node.getparent().remove(node)
            for node in document.iter():
                if node.text:
                    node.text = node.text.strip()
            for node in list(document.iter()):
                if len(node) == 0 and not (node.text or "").strip():
                    parent = node.getparent()
                    if parent is not None:
                        parent.remove(node)
            etree.cleanup_namespaces(document)
        actual_text = etree.tostring(
            actual_document, encoding="unicode", pretty_print=True
        )
        expected_text = etree.tostring(
            expected_document, encoding="unicode", pretty_print=True
        )
        # The builder creates the DTE under an envelope that declares xsi; the
        # oracle fixture's Documento may carry that unused inherited declaration.
        xsi_namespace = ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
        actual_text = actual_text.replace(xsi_namespace, "")
        expected_text = expected_text.replace(xsi_namespace, "")
        diff = "\n".join(
            unified_diff(
                expected_text.splitlines(),
                actual_text.splitlines(),
                fromfile="dte-18",
                tofile="l10n_cl_dte_sii",
                lineterm="",
            )
        )
        self.assertFalse(diff, diff)

    def test_builds_signed_invoice_and_envelope_with_authorized_folio(self):
        journal = self.company_data["default_journal_sale"]
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": journal.id,
                "partner_id": self.partner_a.id,
                "invoice_date": "2026-10-28",
                "l10n_latam_document_type_id": self.document_type.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Servicio de prueba",
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
        caf_root, caf_key = self._caf()
        certificate, certificate_key = self._certificate()
        builder = DteXmlBuilder(invoice, 100, caf_root, certificate)
        dte = builder.build()
        envelope = builder.build_envelope(dte)

        ns = {
            "sii": "http://www.sii.cl/SiiDte",
            "ds": "http://www.w3.org/2000/09/xmldsig#",
        }
        self.assertEqual(dte.xpath("string(.//sii:TipoDTE)", namespaces=ns), "33")
        self.assertEqual(dte.xpath("string(.//sii:Folio)", namespaces=ns), "100")
        self.assertTrue(dte.xpath(".//sii:TED/sii:DD/sii:CAF", namespaces=ns))
        self.assertTrue(envelope.xpath(".//sii:SetDTE/sii:DTE", namespaces=ns))
        self._verify_xml_signature(dte, certificate_key, ns)
        self._verify_xml_signature(envelope, certificate_key, ns)
        dd = etree.fromstring(
            etree.tostring(dte.xpath(".//sii:TED/sii:DD", namespaces=ns)[0])
        )
        for node in dd.iter():
            node.tag = etree.QName(node).localname
        etree.cleanup_namespaces(dd)
        signature = base64.b64decode(
            dte.xpath("string(.//sii:TED/sii:FRMT)", namespaces=ns)
        )
        signed_data = etree.tostring(dd, encoding="ISO-8859-1", xml_declaration=False)
        self.assertTrue(signed_data.startswith(b"<DD>"))
        caf_key.public_key().verify(
            signature, signed_data, padding.PKCS1v15(), hashes.SHA1()
        )

    def test_builds_and_signs_all_in_scope_document_types(self):
        """Smoke every C1 type, including boleta envelopes and credit notes."""
        journal = self.company_data["default_journal_sale"]
        certificate, certificate_key = self._certificate()
        ns = {
            "sii": "http://www.sii.cl/SiiDte",
            "ds": "http://www.w3.org/2000/09/xmldsig#",
        }
        for code in ("33", "34", "39", "41", "56", "61"):
            with self.subTest(document_code=code):
                document_type = self.env["l10n_latam.document.type"].search(
                    [("country_id.code", "=", "CL"), ("code", "=", code)],
                    limit=1,
                )
                self.assertTrue(document_type, f"Missing Chilean type {code}")
                move = self.env["account.move"].create(
                    {
                        "move_type": ("out_refund" if code == "61" else "out_invoice"),
                        "company_id": self.company.id,
                        "journal_id": journal.id,
                        "partner_id": self.partner_a.id,
                        "invoice_date": "2026-10-28",
                        "l10n_latam_document_type_id": document_type.id,
                        "l10n_cl_reference_ids": (
                            [
                                (
                                    0,
                                    0,
                                    {
                                        "l10n_cl_reference_doc_type_id": (
                                            self.document_type.id
                                        ),
                                        "origin_doc_number": "1",
                                        "date": "2026-10-27",
                                        "reference_doc_code": "3",
                                        "reason": "Corrección de prueba",
                                    },
                                )
                            ]
                            if code in ("56", "61")
                            else []
                        ),
                        "invoice_line_ids": [
                            (
                                0,
                                0,
                                {
                                    "name": "Servicio de prueba",
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
                caf_root, _caf_key = self._caf(code)
                dte = DteXmlBuilder(move, 100, caf_root, certificate).build()
                envelope = DteXmlBuilder(
                    move, 100, caf_root, certificate
                ).build_envelope(dte)

                self.assertEqual(
                    dte.xpath("string(.//sii:TipoDTE)", namespaces=ns), code
                )
                self.assertEqual(
                    dte.xpath("string(.//sii:Folio)", namespaces=ns), "100"
                )
                self.assertEqual(
                    len(dte.xpath(".//sii:Referencia", namespaces=ns)),
                    1 if code in ("56", "61") else 0,
                )
                root_name = etree.QName(envelope).localname
                self.assertEqual(
                    root_name, "EnvioBOLETA" if code in ("39", "41") else "EnvioDTE"
                )
                self._verify_xml_signature(dte, certificate_key, ns)
                self._verify_xml_signature(envelope, certificate_key, ns)

    def test_action_post_emits_all_in_scope_document_types(self):
        journal = self.company_data["default_journal_sale"]
        journal.write(
            {
                "l10n_latam_use_documents": True,
                "l10n_cl_dte_point_of_sale": "online",
                "l10n_cl_dte_sucursal": "1",
                "l10n_cl_dte_office_number": "1",
            }
        )
        self.company.write(
            {
                "l10n_cl_dte_environment": "SIIDEMO",
                "l10n_cl_dte_activity": "Servicios de prueba",
                "l10n_cl_dte_activity_code": "620200",
                "l10n_cl_dte_resolution_number": "0",
                "l10n_cl_dte_resolution_date": date(2020, 1, 1),
            }
        )
        certificate, _certificate_key = self._certificate()
        for code in ("33", "34", "39", "41", "56", "61"):
            with self.subTest(document_code=code):
                document_type = self.env["l10n_latam.document.type"].search(
                    [("country_id.code", "=", "CL"), ("code", "=", code)],
                    limit=1,
                )
                self.assertTrue(document_type)
                self._caf(code)
                caf = self.env["l10n_cl.dte.caf"].search(
                    [
                        ("company_id", "=", self.company.id),
                        ("document_type_id", "=", document_type.id),
                    ],
                    limit=1,
                )
                move = self.env["account.move"].create(
                    {
                        "move_type": ("out_refund" if code == "61" else "out_invoice"),
                        "company_id": self.company.id,
                        "journal_id": journal.id,
                        "partner_id": self.partner_a.id,
                        "invoice_date": "2026-10-28",
                        "l10n_latam_document_type_id": document_type.id,
                        "l10n_cl_reference_ids": (
                            [
                                (
                                    0,
                                    0,
                                    {
                                        "l10n_cl_reference_doc_type_id": (
                                            self.document_type.id
                                        ),
                                        "origin_doc_number": "1",
                                        "date": "2026-10-27",
                                        "reference_doc_code": "3",
                                        "reason": "Corrección de prueba",
                                    },
                                )
                            ]
                            if code in ("56", "61")
                            else []
                        ),
                        "invoice_line_ids": [
                            (
                                0,
                                0,
                                {
                                    "name": "Servicio de prueba",
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

                with patch.object(
                    type(self.company),
                    "_get_digital_signature",
                    return_value=certificate,
                ):
                    move.action_post()

                self.assertEqual(move.state, "posted")
                self.assertEqual(move.l10n_latam_document_number, "000100")
                self.assertEqual(move.l10n_cl_dte_folio, 100)
                self.assertEqual(move.l10n_cl_dte_caf_id, caf)
                if "l10n_cl.caf.folio.ledger" in self.env:
                    ledger_entry = (
                        self.env["l10n_cl.caf.folio.ledger"]
                        .sudo()
                        .search([("move_id", "=", move.id)], limit=1)
                    )
                    self.assertEqual(ledger_entry.folio, move.l10n_cl_dte_folio)
                    self.assertEqual(ledger_entry.caf_id, caf)
                self.assertEqual(caf.next_folio, 101)
                self.assertEqual(move.l10n_cl_dte_status, "not_sent")
                self.assertTrue(move.l10n_cl_dte_attachment_id)
                self.assertTrue(move.l10n_cl_dte_envelope_attachment_id)
                dte = base64.b64decode(move.l10n_cl_dte_attachment_id.datas)
                envelope = base64.b64decode(
                    move.l10n_cl_dte_envelope_attachment_id.datas
                )
                self.assertIn(f"<TipoDTE>{code}</TipoDTE>".encode(), dte)
                self.assertIn(b"<Folio>100</Folio>", dte)
                self.assertIn(b"<SetDTE", envelope)

    def test_explicit_receiver_email_uses_new_signed_envelope_and_keeps_sii_copy(self):
        journal = self.company_data["default_journal_sale"]
        journal.write(
            {
                "l10n_latam_use_documents": True,
                "l10n_cl_dte_point_of_sale": "online",
                "l10n_cl_dte_sucursal": "1",
                "l10n_cl_dte_office_number": "1",
            }
        )
        self.company.write(
            {
                "l10n_cl_dte_environment": "SIIDEMO",
                "l10n_cl_dte_activity": "Servicios de prueba",
                "l10n_cl_dte_activity_code": "620200",
                "l10n_cl_dte_resolution_number": "0",
                "l10n_cl_dte_resolution_date": date(2020, 1, 1),
                "l10n_cl_dte_email": "emisor@example.invalid",
            }
        )
        self.partner_a.write(
            {"l10n_cl_dte_email": "receptor@example.invalid", "vat": "76543210-3"}
        )
        self._caf("33", start=100, end=110)
        caf = self.env["l10n_cl.dte.caf"].search(
            [
                ("company_id", "=", self.company.id),
                ("document_type_id", "=", self.document_type.id),
            ],
            limit=1,
        )
        certificate, certificate_key = self._certificate()
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": journal.id,
                "partner_id": self.partner_a.id,
                "invoice_date": "2026-10-28",
                "l10n_latam_document_type_id": self.document_type.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Servicio de prueba",
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
        with patch.object(
            type(self.company), "_get_digital_signature", return_value=certificate
        ):
            move.action_post()
        self.assertEqual(move.l10n_cl_dte_caf_id, caf)
        move._l10n_cl_write_dte_lifecycle_values({"l10n_cl_dte_status": "accepted"})
        original_sii_envelope_bytes = base64.b64decode(
            move.l10n_cl_dte_envelope_attachment_id.datas
        )

        with (
            patch.object(
                type(self.company), "_get_digital_signature", return_value=certificate
            ),
            patch(
                "odoo.addons.l10n_cl_dte_sii.models.account_move.validate_dte_element",
                return_value=True,
            ),
        ):
            move.action_l10n_cl_dte_send_to_receiver()

        delivery = move.l10n_cl_dte_receiver_delivery_ids
        self.assertEqual(len(delivery), 1)
        self.assertEqual(delivery.recipient_email, "receptor@example.invalid")
        self.assertEqual(delivery.state, "outgoing")
        self.assertEqual(delivery.mail_id.email_to, "receptor@example.invalid")
        self.assertIn(delivery.envelope_attachment_id, delivery.mail_id.attachment_ids)
        receiver_xml = base64.b64decode(delivery.envelope_attachment_id.datas)
        root = etree.fromstring(receiver_xml)
        receiver_rut = root.find(".//{http://www.sii.cl/SiiDte}RutReceptor").text
        self.assertEqual(receiver_rut, "76543210-3")
        self._verify_xml_signature(
            root,
            certificate_key,
            {"ds": "http://www.w3.org/2000/09/xmldsig#"},
        )
        self.assertEqual(
            base64.b64decode(move.l10n_cl_dte_envelope_attachment_id.datas),
            original_sii_envelope_bytes,
        )
        self.assertTrue(move.l10n_cl_dte_receiver_delivery_active)
        with self.assertRaises(UserError):
            move.action_l10n_cl_dte_send_to_receiver()
        delivery.mail_id.write({"state": "exception"})
        self.assertEqual(delivery.state, "exception")
        self.assertFalse(move.l10n_cl_dte_receiver_delivery_active)
        with (
            patch.object(
                type(self.company), "_get_digital_signature", return_value=certificate
            ),
            patch(
                "odoo.addons.l10n_cl_dte_sii.models.account_move.validate_dte_element",
                return_value=True,
            ),
        ):
            move.action_l10n_cl_dte_send_to_receiver()
        self.assertEqual(len(move.l10n_cl_dte_receiver_delivery_ids), 2)
        latest_delivery = move.l10n_cl_dte_receiver_delivery_ids.sorted("id")[-1]
        self.assertEqual(latest_delivery.state, "outgoing")
        self.assertTrue(move.l10n_cl_dte_receiver_delivery_active)

    def _verify_xml_signature(self, root, public_key, namespaces):
        signatures = root.findall(".//ds:Signature", namespaces=namespaces)
        self.assertTrue(signatures)
        for signature in signatures:
            signed_info = signature.find("ds:SignedInfo", namespaces=namespaces)
            signature_value = signature.find("ds:SignatureValue", namespaces=namespaces)
            public_key.public_key().verify(
                base64.b64decode(signature_value.text),
                etree.tostring(signed_info, method="c14n", with_comments=False),
                padding.PKCS1v15(),
                hashes.SHA1(),
            )
            reference = signed_info.find(".//ds:Reference", namespaces=namespaces)
            reference_id = reference.get("URI", "").removeprefix("#")
            signed_node = next(
                node for node in root.iter() if node.get("ID") == reference_id
            )
            digest = base64.b64encode(
                hashlib.sha1(
                    etree.tostring(signed_node, method="c14n", with_comments=False)
                ).digest()
            ).decode()
            self.assertEqual(
                signed_info.find(".//ds:DigestValue", namespaces=namespaces).text,
                digest,
            )
