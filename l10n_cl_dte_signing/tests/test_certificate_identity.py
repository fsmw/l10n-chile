# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.x509.oid import NameOID

from odoo.exceptions import ValidationError

from ..services.certificate_identity import (
    HOLDER_RUT_OID,
    _der_string,
    certificate_sender_rut,
)
from .common import SigningTestCase


class TestCertificateIdentity(SigningTestCase):
    def certificate(self, subject_rut=None, alt_rut=None, oid=HOLDER_RUT_OID):
        attributes = [x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic identity")]
        if subject_rut:
            attributes.append(x509.NameAttribute(NameOID.SERIAL_NUMBER, subject_rut))
        name = x509.Name(attributes)
        builder = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(self.caf_test_key.public_key())
            .serial_number(123)
            .not_valid_before(datetime(2025, 1, 1))
            .not_valid_after(datetime(2099, 1, 1))
        )
        if alt_rut is not None:
            builder = builder.add_extension(
                x509.SubjectAlternativeName([x509.OtherName(oid, alt_rut)]),
                critical=False,
            )
        return builder.sign(self.caf_test_key, hashes.SHA256())

    def test_chilean_other_name_and_leading_zero(self):
        certificate = self.certificate(alt_rut=b"\x16\x0b012345678-5")
        self.assertEqual(certificate_sender_rut(certificate), "12345678-5")

    def test_serial_number_and_other_name_must_agree(self):
        certificate = self.certificate(
            subject_rut="12345678-5", alt_rut=b"\x16\x0a76086428-5"
        )
        with self.assertRaises(ValidationError):
            certificate_sender_rut(certificate)

    def test_missing_wrong_oid_and_invalid_check_digit_are_rejected(self):
        certificates = (
            self.certificate(),
            self.certificate(subject_rut="12345678-6"),
            self.certificate(
                alt_rut=b"\x16\x0a12345678-5",
                oid=x509.ObjectIdentifier("1.2.3.4"),
            ),
        )
        for certificate in certificates:
            with (
                self.subTest(serial=certificate.serial_number),
                self.assertRaises(ValidationError),
            ):
                certificate_sender_rut(certificate)

    def test_trailing_der_bytes_are_rejected(self):
        with self.assertRaises(ValueError):
            _der_string(b"\x16\x0a12345678-5\x05\x00")
