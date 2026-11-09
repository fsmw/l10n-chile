# Copyright 2026 Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import Mock, patch

from odoo.tests import TransactionCase

from odoo.addons.l10n_cl_dte_gateway.services.production_gateway import (
    SUPPORTED_PRODUCTION_DTE_CODES,
    ProductionSiiGateway,
)
from odoo.addons.l10n_cl_dte_gateway.services.production_transport import (
    ProductionSiiTransport,
)


class TestProductionSiiTransport(TransactionCase):
    """Exercise the production upload boundary with a stubbed HTTP client."""

    def test_production_envelope_gateway_is_selected(self):
        gateway = self.env["l10n_cl.sii.gateway.factory"].get_envelope_gateway(
            "production"
        )
        self.assertIsInstance(gateway, ProductionSiiTransport)

    def test_production_document_gateway_is_selected(self):
        gateway = self.env["l10n_cl.sii.gateway.factory"].get_gateway("production")
        self.assertIsInstance(gateway, ProductionSiiGateway)
        self.assertNotIsInstance(
            gateway, type(self.env["l10n_cl.sii.gateway.factory"].get_gateway("demo"))
        )
        self.assertIn("52", SUPPORTED_PRODUCTION_DTE_CODES)

    def test_query_status_maps_one_document_result(self):
        transport = Mock()
        transport.query_envelope.return_value = {
            "state": "processing",
            "track_id": "12345",
            "counts": [
                {
                    "INFORMADOS": 1,
                    "ACEPTADOS": 1,
                    "RECHAZADOS": 0,
                    "REPAROS": 0,
                }
            ],
        }
        gateway = ProductionSiiGateway(transport_factory=lambda: transport)
        company = Mock()

        result = gateway.query_status(
            company, "production", "12345", "scenario", 1, "submission-1"
        )

        self.assertEqual(result["state"], "accepted")
        self.assertFalse(result["is_simulated"])
        transport.query_envelope.assert_called_once_with(company, "production", "12345")

    def test_rut_components_are_normalized_for_the_upload_form(self):
        self.assertEqual(ProductionSiiTransport._rut("60.803.000-k"), ("60803000", "K"))

    def test_upload_uses_signed_bytes_and_returns_bound_receipt(self):
        response = (
            b"<RECEPCIONDTE><STATUS>0</STATUS><TRACKID>12345</TRACKID>"
            b"<RUTCOMPANY>60803000-K</RUTCOMPANY>"
            b"<RUTSENDER>60803000-K</RUTSENDER></RECEPCIONDTE>"
        )
        auth = Mock()
        auth.token.return_value = "opaque-token"
        auth.post.return_value = response
        transport = ProductionSiiTransport(authentication_factory=lambda: auth)
        company = Mock()
        with patch.object(
            transport, "validate_request", return_value=("60803000-K", "60803000-K")
        ):
            result = transport.send_envelope(
                company, "production", b"<signed-envelope/>", "submission-1"
            )

        self.assertEqual(result["state"], "submitted")
        self.assertEqual(result["track_id"], "12345")
        self.assertEqual(result["raw_response"], response)
        auth.post.assert_called_once()
        self.assertEqual(auth.post.call_args.args[0], "/cgi_dte/UPL/DTEUpload")
        self.assertEqual(
            auth.post.call_args.kwargs["files"]["archivo"][1], b"<signed-envelope/>"
        )
        auth.close.assert_called_once()
