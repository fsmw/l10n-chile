import base64
import re
import xml.etree.ElementTree as ElementTree
from datetime import date

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from odoo import fields

from odoo.addons.l10n_cl_dte_core.tests.common import DteTestCase


class CafTestCase(DteTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sii_test_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        cls.caf_test_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        cls.sii_key_id = "987654321"
        cls.sii_public_pem = (
            cls.sii_test_key.public_key()
            .public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("ascii")
        )
        cls.caf_private_pem = cls.caf_test_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ).decode("ascii")
        cls.caf_public_pem = (
            cls.caf_test_key.public_key()
            .public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("ascii")
        )
        cls.caf_public_numbers = cls.caf_test_key.public_key().public_numbers()

    def create_trust_key(self):
        model = self.env["l10n_cl.caf.trust.key"].with_user(self.dte_manager)
        existing = model.search(
            [
                ("key_id", "=", self.sii_key_id),
                ("scope", "=", "demo"),
                ("valid_from", "=", fields.Date.today()),
            ],
            limit=1,
        )
        if existing:
            return existing
        return model.create(
            {
                "name": "Synthetic test trust key",
                "key_id": self.sii_key_id,
                "scope": "demo",
                "is_synthetic": True,
                "algorithm": "SHA1withRSA",
                "public_key_pem": self.sii_public_pem,
                "valid_from": fields.Date.today(),
                "source_url": "https://example.invalid/synthetic-test-key",
            }
        )

    def build_caf_xml(
        self,
        document_code="33",
        folio_start=100,
        folio_end=103,
        issuer_vat="76864280-K",
        date_from=None,
        key_id=None,
    ):
        signed_date = date.fromisoformat(str(date_from or fields.Date.today()))
        public_numbers = self.caf_public_numbers
        data = ElementTree.Element("DA")
        ElementTree.SubElement(data, "RE").text = issuer_vat
        ElementTree.SubElement(data, "RS").text = "Synthetic Test Issuer"
        ElementTree.SubElement(data, "TD").text = str(document_code)
        range_node = ElementTree.SubElement(data, "RNG")
        ElementTree.SubElement(range_node, "D").text = str(folio_start)
        ElementTree.SubElement(range_node, "H").text = str(folio_end)
        ElementTree.SubElement(data, "FA").text = signed_date.isoformat()
        rsapk = ElementTree.SubElement(data, "RSAPK")
        modulus = public_numbers.n.to_bytes(
            (public_numbers.n.bit_length() + 7) // 8, "big"
        )
        exponent = public_numbers.e.to_bytes(
            (public_numbers.e.bit_length() + 7) // 8, "big"
        )
        ElementTree.SubElement(rsapk, "M").text = base64.b64encode(modulus).decode()
        ElementTree.SubElement(rsapk, "E").text = base64.b64encode(exponent).decode()
        ElementTree.SubElement(data, "IDK").text = key_id or self.sii_key_id
        signed_bytes = ElementTree.tostring(
            data, encoding="ISO-8859-1", short_empty_elements=True
        )
        signed_bytes = re.sub(rb">[\r\n\t ]+<", b"><", signed_bytes)
        sii_signature = self.sii_test_key.sign(
            signed_bytes, padding.PKCS1v15(), hashes.SHA1()
        )
        authorization = ElementTree.Element("AUTORIZACION")
        caf = ElementTree.SubElement(authorization, "CAF", version="1.0")
        caf.append(data)
        signature = ElementTree.SubElement(caf, "FRMA", algoritmo="SHA1withRSA")
        signature.text = base64.b64encode(sii_signature).decode("ascii")
        ElementTree.SubElement(authorization, "RSASK").text = self.caf_private_pem
        ElementTree.SubElement(authorization, "RSAPUBK").text = self.caf_public_pem
        return ElementTree.tostring(
            authorization, encoding="ISO-8859-1", xml_declaration=True
        )

    def create_authorization(
        self,
        document_code="33",
        folio_start=100,
        folio_end=103,
        file_document_code=None,
        date_to=False,
        company=None,
        issuer_vat="76864280-K",
        caf_bytes=None,
        key_id=None,
    ):
        company = company or self.company
        company.partner_id.sudo().write({"vat": issuer_vat})
        if key_id in (None, self.sii_key_id):
            self.create_trust_key()
        caf_bytes = caf_bytes or self.build_caf_xml(
            document_code=file_document_code or document_code,
            folio_start=folio_start,
            folio_end=folio_end,
            issuer_vat=issuer_vat,
            key_id=key_id,
        )
        return (
            self.env["l10n_cl.caf.authorization"]
            .with_user(self.dte_manager)
            .with_company(company)
            .create(
                {
                    "name": f"Synthetic CAF {document_code} {folio_start}",
                    "company_id": company.id,
                    "sii_environment": "demo",
                    "is_synthetic": True,
                    "document_code": str(document_code),
                    "issuer_vat": issuer_vat,
                    "folio_start": folio_start,
                    "folio_end": folio_end,
                    "date_from": fields.Date.today(),
                    "date_to": date_to,
                    "caf_file": base64.b64encode(caf_bytes),
                }
            )
        )

    def validate_authorization(self, **values):
        authorization = self.create_authorization(**values)
        authorization.action_validate_authorization()
        return authorization
