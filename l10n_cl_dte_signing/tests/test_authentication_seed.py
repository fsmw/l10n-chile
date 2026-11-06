import base64
import hashlib
from copy import deepcopy

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from lxml import etree

from odoo.exceptions import AccessError, UserError, ValidationError

from ..services.signature_service import SignatureService
from .common import SigningTestCase


class TestAuthenticationSeed(SigningTestCase):
    def test_signed_seed_digest_and_signature(self):
        self.create_signing_profile(environment="certification")
        content = SignatureService().sign_authentication_seed(
            self.company.with_user(self.dte_manager), "certification", "004200"
        )
        root = etree.fromstring(content)
        self.assertEqual(root.tag, "getToken")
        self.assertEqual(root.findtext("item/Semilla"), "004200")
        signature = root[-1]
        info = signature[0]
        unsigned = deepcopy(root)
        unsigned.remove(unsigned[-1])
        expected = base64.b64encode(
            hashlib.sha1(etree.tostring(unsigned, method="c14n")).digest()
        ).decode()
        self.assertEqual(
            info.xpath('.//*[local-name()="DigestValue"]/text()'), [expected]
        )
        self.caf_test_key.public_key().verify(
            base64.b64decode(signature[1].text),
            etree.tostring(etree.fromstring(etree.tostring(info)), method="c14n"),
            padding.PKCS1v15(),
            hashes.SHA1(),
        )
        self.assertEqual(len(root.xpath("./*[local-name()='Signature']")), 1)

    def test_only_a_numeric_certification_seed_is_supported(self):
        company = self.company.with_user(self.dte_manager)
        for environment, seed in [
            ("demo", "42"),
            ("production", "42"),
            ("certification", "invalid"),
            ("certification", ""),
        ]:
            with self.subTest(environment=environment, seed=seed):
                with self.assertRaises(ValidationError):
                    SignatureService().sign_authentication_seed(
                        company, environment, seed
                    )

    def test_operator_cannot_use_the_private_key(self):
        self.create_signing_profile(environment="certification")
        with self.assertRaises(AccessError):
            SignatureService().sign_authentication_seed(
                self.company.with_user(self.dte_user), "certification", "42"
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
