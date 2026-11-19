from odoo.exceptions import UserError, ValidationError

from ..services.signature_service import SignatureService
from .common import SigningTestCase


class TestAuthenticationSeed(SigningTestCase):
    def test_only_a_numeric_seed_for_a_real_environment_is_supported(self):
        company = self.company.with_user(self.dte_manager)
        for environment, seed in [
            ("demo", "42"),
            ("certification", "invalid"),
            ("certification", ""),
        ]:
            with self.subTest(environment=environment, seed=seed):
                with self.assertRaises(ValidationError):
                    SignatureService().sign_authentication_seed(
                        company, environment, seed
                    )

    def test_missing_or_inactive_signing_profile_is_rejected(self):
        company = self.company.with_user(self.dte_manager)
        with self.assertRaisesRegex(UserError, "signing profile"):
            SignatureService().sign_authentication_seed(company, "certification", "42")
        profile = self.create_signing_profile(environment="certification")
        profile.write({"active": False})
        with self.assertRaisesRegex(UserError, "signing profile"):
            SignatureService().sign_authentication_seed(company, "certification", "42")

    def test_certificate_without_private_key_is_rejected(self):
        self.create_signing_profile(environment="certification", private=False)
        with self.assertRaisesRegex(UserError, "private key"):
            SignatureService().sign_authentication_seed(
                self.company.with_user(self.dte_manager), "certification", "42"
            )
