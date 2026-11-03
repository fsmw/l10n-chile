from datetime import date

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from odoo.exceptions import ValidationError

from .common import CafTestCase


class TestCafTrustKey(CafTestCase):
    def test_signature_accepts_trusted_key(self):
        trust_key = self.create_trust_key()
        signed_data = b"synthetic CAF authorization data"
        signature = self.sii_test_key.sign(
            signed_data, padding.PKCS1v15(), hashes.SHA1()
        )

        verified_key = (
            self.env["l10n_cl.caf.trust.key"]
            .with_user(self.dte_manager)
            .action_verify_caf_signature(
                self.sii_key_id, signed_data, signature, date.today(), scope="demo"
            )
        )

        self.assertEqual(verified_key, trust_key)
        self.assertEqual(trust_key.fingerprint, trust_key.fingerprint.lower())

    def test_wrong_signer_or_scope_rejected(self):
        self.create_trust_key()
        signed_data = b"synthetic CAF authorization data"
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        wrong_signature = other_key.sign(signed_data, padding.PKCS1v15(), hashes.SHA1())
        trust_keys = self.env["l10n_cl.caf.trust.key"].with_user(self.dte_manager)

        with self.assertRaises(ValidationError):
            trust_keys.action_verify_caf_signature(
                self.sii_key_id, signed_data, wrong_signature, date.today(), "demo"
            )
        with self.assertRaises(ValidationError):
            trust_keys.action_verify_caf_signature(
                self.sii_key_id, signed_data, wrong_signature, date.today(), "official"
            )

    def test_malformed_caf_redacts_secrets(self):
        authorization = self.create_authorization(caf_bytes=b"<AUTORIZACION")

        with self.assertRaises(ValidationError) as error:
            authorization.action_validate_authorization()

        self.assertNotIn(self.caf_private_pem, str(error.exception))
        self.assertIn("well-formed XML", str(error.exception))
