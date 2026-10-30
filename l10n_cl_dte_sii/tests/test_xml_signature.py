# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from lxml import etree

from odoo.tests import TransactionCase, tagged

from ..services.xml_signature import DSIG_NS, add_xml_signature, canonical_xml


@tagged("post_install", "-at_install")
class TestDteXmlSignature(TransactionCase):
    def test_signature_digest_and_signature_value_cover_expected_xml(self):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        root = etree.Element("DTE")
        document = etree.SubElement(root, "Documento", ID="F1T33")
        etree.SubElement(document, "Folio").text = "1"
        public_numbers = private_key.public_key().public_numbers()
        modulus = base64.b64encode(
            public_numbers.n.to_bytes((public_numbers.n.bit_length() + 7) // 8, "big")
        )
        exponent = base64.b64encode(
            public_numbers.e.to_bytes((public_numbers.e.bit_length() + 7) // 8, "big")
        )
        certificate_der = base64.b64encode(b"synthetic-cert")

        def sign(data):
            signature = private_key.sign(data, padding.PKCS1v15(), hashes.SHA1())
            return base64.b64encode(signature)

        add_xml_signature(
            root,
            reference_id="F1T33",
            sign=sign,
            modulus=modulus,
            exponent=exponent,
            certificate_der=certificate_der,
        )

        signature = root.find(f"{{{DSIG_NS}}}Signature")
        signed_info = signature.find(f"{{{DSIG_NS}}}SignedInfo")
        digest_value = signed_info.find(f".//{{{DSIG_NS}}}DigestValue").text
        expected_digest = base64.b64encode(
            hashlib.sha1(canonical_xml(document)).digest()
        )
        self.assertEqual(digest_value, expected_digest.decode())
        private_key.public_key().verify(
            base64.b64decode(signature.find(f"{{{DSIG_NS}}}SignatureValue").text),
            canonical_xml(signed_info),
            padding.PKCS1v15(),
            hashes.SHA1(),
        )

    def test_signature_rejects_missing_reference_target(self):
        root = etree.Element("DTE")
        with self.assertRaisesRegex(ValueError, "does not exist"):
            add_xml_signature(
                root,
                reference_id="missing",
                sign=lambda data: b"signature",
                modulus=b"AA==",
                exponent=b"AQAB",
                certificate_der=b"AA==",
            )
