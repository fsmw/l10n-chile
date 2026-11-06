import base64

from lxml import etree

from odoo.exceptions import AccessError, ValidationError

from odoo.addons.l10n_cl_dte_signing.services.signature_service import (
    SignatureService,
    _canonicalize,
)

from .common import SigningTestCase


class TestSignatureService(SigningTestCase):
    def test_dte_user_signs_only_through_privileged_issuance_context(self):
        self.create_signing_profile()
        company = self.company.with_user(self.dte_user)
        service = SignatureService()
        with self.assertRaises(AccessError):
            service.sign_document(company, "demo", self.unsigned_xml())
        signed = service.sign_document(company.sudo(), "demo", self.unsigned_xml())
        self.assertTrue(service.verify_signature(signed)["valid"])

    def test_ted_signature_round_trip(self):
        authorization = self.validate_authorization()
        service = SignatureService()
        dd = b"<DD><RE>76864280-K</RE><TD>33</TD><F>100</F></DD>"

        signature = service.sign_ted(self.company, authorization, dd)
        caf_node = etree.fromstring(base64.b64decode(authorization.caf_file)).find(
            "CAF"
        )

        self.assertTrue(service.verify_ted(dd, signature, caf_node))

    def test_dte_xml_signature_round_trip(self):
        self.create_signing_profile()
        service = SignatureService()

        signed = service.sign_document(
            self.company.with_user(self.dte_manager), "demo", self.unsigned_xml()
        )

        self.assertTrue(service.verify_signature(signed)["valid"])

    def test_dte_signature_survives_standalone_sii_extraction(self):
        self.create_signing_profile()
        service = SignatureService()
        unsigned = self.unsigned_xml().replace(
            b"<DTE>",
            b'<DTE xmlns="http://www.sii.cl/SiiDte" '
            b'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">',
            1,
        )
        signed = service.sign_document(
            self.company.with_user(self.dte_manager), "demo", unsigned
        )
        self.assertNotIn(b"xmlns:xsi", signed)
        wrapper = etree.fromstring(
            b'<EnvioDTE xmlns="http://www.sii.cl/SiiDte" '
            b'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><SetDTE/>'
            b"</EnvioDTE>"
        )
        wrapper[0].append(etree.fromstring(signed))
        embedded = etree.tostring(wrapper[0][0], encoding="ISO-8859-1")
        result = service.verify_signature(embedded, sii_standalone=True)
        self.assertEqual(result["document_digest_canonicalization"], "inclusive")
        self.assertEqual(result["signed_info_canonicalization"], "inclusive")

    def test_tampered_signature_payload_rejected(self):
        self.create_signing_profile()
        service = SignatureService()
        signed = service.sign_document(
            self.company.with_user(self.dte_manager), "demo", self.unsigned_xml()
        )
        tampered = signed.replace(b">123<", b">999<")

        with self.assertRaises(ValidationError):
            service.verify_signature(tampered)
        with self.assertRaises(ValidationError):
            service.verify_ted(
                b"<DD><RE>76864280-K</RE><TD>33</TD><F>101</F></DD>",
                "invalid-signature",
                etree.fromstring(
                    base64.b64decode(self.validate_authorization().caf_file)
                ).find("CAF"),
            )

    def test_signing_identity_mismatch_rejected(self):
        certificate = self.create_certificate()
        other_company = (
            self.env["res.company"].sudo().create({"name": "Other signer test company"})
        )
        profile = (
            self.env["l10n_cl.dte.signing.profile"].with_user(self.dte_manager).sudo()
        )

        with self.assertRaises(ValidationError):
            profile.create(
                {
                    "name": "Mismatched signing profile",
                    "company_id": other_company.id,
                    "sii_environment": "demo",
                    "certificate_id": certificate.id,
                    "is_synthetic": True,
                }
            )

    def test_inclusive_c14n_does_not_reset_nested_default_namespace(self):
        root = etree.fromstring(
            b'<request><Signature xmlns="http://www.w3.org/2000/09/xmldsig#">'
            b'<SignedInfo><Reference URI=""><Transforms><Transform Algorithm="test"/>'
            b"</Transforms><DigestValue>digest</DigestValue></Reference></SignedInfo>"
            b"</Signature></request>"
        )
        expected = (
            b'<SignedInfo xmlns="http://www.w3.org/2000/09/xmldsig#">'
            b'<Reference URI=""><Transforms><Transform Algorithm="test"></Transform>'
            b"</Transforms><DigestValue>digest</DigestValue></Reference></SignedInfo>"
        )
        self.assertEqual(_canonicalize(root[0][0]), expected)

    def test_c14n_preserves_unused_in_scope_namespaces_only_when_inclusive(self):
        root = etree.fromstring(
            b'<request xmlns:extra="urn:extra"><Signature xmlns="urn:signature">'
            b"<SignedInfo><Reference/></SignedInfo></Signature></request>"
        )
        info = root[0][0]
        self.assertEqual(
            _canonicalize(info),
            b'<SignedInfo xmlns="urn:signature" xmlns:extra="urn:extra">'
            b"<Reference></Reference></SignedInfo>",
        )
        self.assertEqual(
            _canonicalize(info, exclusive=True),
            b'<SignedInfo xmlns="urn:signature"><Reference></Reference></SignedInfo>',
        )

    def test_inclusive_c14n_preserves_nearest_inherited_xml_attributes(self):
        root = etree.fromstring(
            b'<request xml:lang="es-CL" xml:space="preserve"><Signature '
            b'xmlns="urn:signature" xml:lang="en"><SignedInfo>'
            b"<Reference/></SignedInfo></Signature></request>"
        )
        info = root[0][0]
        self.assertEqual(
            _canonicalize(info),
            b'<SignedInfo xmlns="urn:signature" xml:lang="en" xml:space="preserve">'
            b"<Reference></Reference></SignedInfo>",
        )
        self.assertEqual(
            _canonicalize(info, exclusive=True),
            b'<SignedInfo xmlns="urn:signature"><Reference></Reference></SignedInfo>',
        )
