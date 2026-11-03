from odoo.exceptions import UserError

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService

from .common import SigningTestCase


class TestSigningProfile(SigningTestCase):
    def test_profile_selection_scoped_to_company_env(self):
        profile = self.create_signing_profile()
        company = self.company.with_user(self.dte_manager)

        signed = SignatureService().sign_document(company, "demo", self.unsigned_xml())
        result = SignatureService().verify_signature(signed)

        self.assertTrue(profile.active)
        self.assertTrue(result["valid"])
        with self.assertRaises(UserError):
            SignatureService().sign_document(
                company, "certification", self.unsigned_xml()
            )

    def test_inactive_or_expired_profile_rejected(self):
        profile = self.create_signing_profile()
        profile.with_user(self.dte_manager).write({"active": False})

        with self.assertRaises(UserError):
            SignatureService().sign_document(
                self.company.with_user(self.dte_manager), "demo", self.unsigned_xml()
            )

    def test_profile_requires_private_key(self):
        profile = self.create_signing_profile(private=False)
        self.assertFalse(profile.certificate_id.private_key_id)

        with self.assertRaises(UserError):
            SignatureService().sign_document(
                self.company.with_user(self.dte_manager), "demo", self.unsigned_xml()
            )
