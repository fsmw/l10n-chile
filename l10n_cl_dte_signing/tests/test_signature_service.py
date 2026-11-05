import base64

from lxml import etree

from odoo.exceptions import AccessError, ValidationError

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService

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
