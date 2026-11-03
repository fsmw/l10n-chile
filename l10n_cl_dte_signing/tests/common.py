import base64
from datetime import datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from odoo.addons.l10n_cl_dte_caf.tests.common import CafTestCase


class SigningTestCase(CafTestCase):
    def create_certificate(self, company=None, include_private_key=True):
        company = company or self.company
        key = self.caf_test_key
        name = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "DTE synthetic test signer")]
        )
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(81001)
            .not_valid_before(datetime(2025, 1, 1))
            .not_valid_after(datetime(2099, 1, 1))
            .sign(key, hashes.SHA256())
        )
        if include_private_key:
            content = pkcs12.serialize_key_and_certificates(
                b"synthetic-test-signer",
                key,
                certificate,
                None,
                serialization.NoEncryption(),
            )
        else:
            content = certificate.public_bytes(serialization.Encoding.PEM)
        return (
            self.env["certificate.certificate"]
            .sudo()
            .create(
                {
                    "name": "Synthetic DTE test signer",
                    "company_id": company.id,
                    "content": base64.b64encode(content),
                }
            )
        )

    def create_signing_profile(self, company=None, environment="demo", private=True):
        company = company or self.company
        certificate = self.create_certificate(
            company=company, include_private_key=private
        )
        profile = (
            self.env["l10n_cl.dte.signing.profile"]
            .with_user(self.dte_manager)
            .with_company(company)
            .create(
                {
                    "name": "Synthetic DTE test profile",
                    "company_id": company.id,
                    "sii_environment": environment,
                    "certificate_id": certificate.id,
                    "is_synthetic": environment == "demo",
                }
            )
        )
        return profile

    def unsigned_xml(self):
        return (
            b'<?xml version="1.0" encoding="ISO-8859-1"?>'
            b'<DTE><Documento ID="DTE-test-1"><Monto>123</Monto>'
            b"</Documento></DTE>"
        )
