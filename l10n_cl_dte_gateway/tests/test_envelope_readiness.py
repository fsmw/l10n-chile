from unittest.mock import Mock, patch

from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_gateway.services.envelope_composer import (
    SiiEnvelopeComposer,
)
from odoo.addons.l10n_cl_dte_signing.tests.common import SigningTestCase


class TestSiiEnvelopeReadiness(SigningTestCase):
    def setUp(self):
        super().setUp()
        self.company = self.company.with_user(self.dte_manager)
        self.company.sudo().write({"dte_sii_environment": "certification"})
        self.create_signing_profile(environment="certification")
        self.gateway_factory = self.env["l10n_cl.sii.gateway.factory"].with_user(
            self.dte_manager
        )

    def test_sender_identity_is_normalized_and_matches_certificate(self):
        sender_vat = SiiEnvelopeComposer().validate_sender_identity(
            self.company, "12.345.678-5"
        )

        self.assertEqual(sender_vat, "12345678-5")

    def test_compose_rejects_a_different_sender_before_signing(self):
        signature_service = Mock()
        signature_service.certification_signing_identity.return_value = (
            object(),
            "12345678-5",
        )
        with self.assertRaisesRegex(ValidationError, "must match"):
            SiiEnvelopeComposer().compose(
                self.company,
                [b"<synthetic DTE />"],
                {
                    "id": "Set-test",
                    "sender_vat": "87.654.321-4",
                    "resolution_number": "0",
                    "resolution_date": "2026-01-01",
                },
                signature_service=signature_service,
            )

        signature_service.sign_envelope.assert_not_called()

    def test_mismatched_sender_blocks_readiness_before_transport_lookup(self):
        with patch.object(
            type(self.gateway_factory), "get_envelope_gateway"
        ) as get_gateway:
            readiness = self.gateway_factory.check_envelope_readiness(
                self.company.id, "87.654.321-4"
            )

        self.assertFalse(readiness["ready"])
        self.assertIn("must match", readiness["diagnostics"][0])
        get_gateway.assert_not_called()

    def test_matching_sender_stays_blocked_without_verified_transport(self):
        readiness = self.gateway_factory.check_envelope_readiness(
            self.company.id, "12345678-5"
        )

        self.assertFalse(readiness["ready"])
        self.assertIn(
            "No verified multi-DTE SII transport", readiness["diagnostics"][0]
        )

    def test_environment_mismatch_blocks_readiness(self):
        with patch.object(
            type(self.gateway_factory), "get_envelope_gateway"
        ) as get_gateway:
            readiness = self.gateway_factory.check_envelope_readiness(
                self.company.id, "12345678-5", "production"
            )

        self.assertFalse(readiness["ready"])
        self.assertIn("differs from the company mode", readiness["diagnostics"][0])
        get_gateway.assert_not_called()

    def test_readiness_passes_only_with_identity_and_verified_transport(self):
        gateway = Mock()
        with patch.object(
            type(self.gateway_factory), "get_envelope_gateway", return_value=gateway
        ):
            readiness = self.gateway_factory.check_envelope_readiness(
                self.company.id, "12345678-5"
            )

        self.assertEqual(readiness, {"ready": True, "diagnostics": []})
        gateway.validate_configuration.assert_called_once_with(
            self.company, "certification"
        )

    def test_sender_rut_is_read_from_envelope(self):
        envelope = (
            b'<EnvioDTE xmlns="http://www.sii.cl/SiiDte"><SetDTE>'
            b"<Caratula><RutEnvia>12345678-5</RutEnvia></Caratula>"
            b"</SetDTE></EnvioDTE>"
        )

        self.assertEqual(SiiEnvelopeComposer().get_sender_vat(envelope), "12345678-5")

    def test_sender_rut_extraction_rejects_dtd(self):
        envelope = (
            b'<!DOCTYPE EnvioDTE [<!ENTITY sender "12345678-5">]>'
            b'<EnvioDTE xmlns="http://www.sii.cl/SiiDte"><SetDTE>'
            b"<Caratula><RutEnvia>&sender;</RutEnvia></Caratula>"
            b"</SetDTE></EnvioDTE>"
        )

        with self.assertRaisesRegex(ValidationError, "without a DTD"):
            SiiEnvelopeComposer().get_sender_vat(envelope)
