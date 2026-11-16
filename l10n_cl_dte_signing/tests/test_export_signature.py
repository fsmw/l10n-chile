# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from copy import deepcopy

from lxml import etree

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from ..services.signature_service import DS_NAMESPACE, SignatureService, _canonicalize
from .common import SigningTestCase

DS = f"{{{DS_NAMESPACE}}}"


@tagged("post_install", "-at_install")
class TestExportSignature(SigningTestCase):
    def setUp(self):
        super().setUp()
        self.profile = self.create_signing_profile()
        self.service = SignatureService()
        self.signer = self.company.with_user(self.dte_manager)

    def _unsigned(self, code="110"):
        return (
            self.unsigned_xml()
            .replace(b"Documento", b"Exportaciones")
            .replace(b">33<", f">{code}<".encode())
        )

    def _sign(self, content):
        return self.service.sign_document(self.signer, "demo", content)

    def test_exports_round_trip_and_bind_exact_payload(self):
        for code in ("110", "111", "112"):
            with self.subTest(code=code):
                signed = self._sign(self._unsigned(code))
                root = etree.fromstring(signed)
                references = root.findall(f"{DS}Signature/{DS}SignedInfo/{DS}Reference")
                self.assertEqual(len(references), 1)
                self.assertEqual(references[0].get("URI"), "#DTE-test-1")
                self.assertTrue(
                    self.service.verify_signature(signed, sii_standalone=True)["valid"]
                )
                with self.assertRaises(ValidationError):
                    self.service.verify_signature(signed.replace(b">123<", b">999<"))

    def test_ambiguous_payload_rejected_by_signer_and_verifier(self):
        unsigned = self.unsigned_xml()
        signed = self._sign(unsigned)
        for content in (unsigned, signed):
            variants = [
                content.replace(b"</DTE>", b"<Exportaciones ID='extra'/></DTE>"),
                content.replace(b"<DTE ", b'<DTE ID="DTE-test-1" '),
                content.replace(b"</Documento>", b"<Exportaciones/></Documento>"),
                content.replace(b"http://www.sii.cl/SiiDte", b"urn:foreign"),
            ]
            for index, xml in enumerate(variants):
                with self.subTest(signed=content == signed, case=index):
                    with self.assertRaises(ValidationError):
                        if content == unsigned:
                            self._sign(xml)
                        else:
                            self.service.verify_signature(xml)

    def test_signed_duplicate_or_wrong_reference_rejected(self):
        signed = self._sign(self.unsigned_xml())
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate):
                root = etree.fromstring(signed)
                signature = root.find(f"{DS}Signature")
                info = signature.find(f"{DS}SignedInfo")
                reference = info.find(f"{DS}Reference")
                if duplicate:
                    info.append(deepcopy(reference))
                else:
                    reference.set("URI", "#unrelated")
                signature.find(f"{DS}SignatureValue").text = (
                    self.profile.sudo()
                    .certificate_id._sign(
                        _canonicalize(info),
                        hashing_algorithm="sha1",
                        formatting="base64",
                    )
                    .decode("ascii")
                )
                with self.assertRaises(ValidationError):
                    self.service.verify_signature(etree.tostring(root))

    def test_duplicate_signature_elements_rejected(self):
        signed = self._sign(self.unsigned_xml())
        for name in ("SignedInfo", "SignatureValue", "DigestValue", "Reference"):
            with self.subTest(name=name):
                root = etree.fromstring(signed)
                node = root.find(f".//{DS}{name}")
                node.addnext(deepcopy(node))
                signature = root.find(f"{DS}Signature")
                info = signature.find(f"{DS}SignedInfo")
                signature.find(f"{DS}SignatureValue").text = (
                    self.profile.sudo()
                    .certificate_id._sign(
                        _canonicalize(info),
                        hashing_algorithm="sha1",
                        formatting="base64",
                    )
                    .decode("ascii")
                )
                with self.assertRaises(ValidationError):
                    self.service.verify_signature(etree.tostring(root))
