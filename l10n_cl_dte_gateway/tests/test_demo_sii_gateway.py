from odoo.exceptions import UserError

from odoo.addons.l10n_cl_dte_gateway.services.sii_gateway import DemoSiiGateway

from .common import GatewayTestCase


class TestDemoSiiGateway(GatewayTestCase):
    def test_demo_send_track_id_deterministic(self):
        gateway = DemoSiiGateway()
        first = gateway.send_document(
            self.company, "demo", b"synthetic XML", "key-1", "accepted"
        )
        second = gateway.send_document(
            self.company, "demo", b"different bytes", "key-1", "accepted"
        )

        self.assertEqual(first["track_id"], second["track_id"])
        self.assertEqual(first["state"], "submitted")
        self.assertTrue(first["is_simulated"])

    def test_demo_query_returns_terminal_status(self):
        gateway = DemoSiiGateway()
        track_id = gateway.send_document(
            self.company,
            "demo",
            b"synthetic XML",
            "query-key",
            "processing_then_accepted",
        )["track_id"]
        first = gateway.query_status(
            self.company, "demo", track_id, "processing_then_accepted", 1, "query-key"
        )
        second = gateway.query_status(
            self.company, "demo", track_id, "processing_then_accepted", 2, "query-key"
        )

        self.assertEqual(first["state"], "processing")
        self.assertEqual(second["state"], "accepted")

    def test_demo_failure_results_reproducible(self):
        gateway = DemoSiiGateway()
        rejected_track = gateway.send_document(
            self.company, "demo", b"synthetic XML", "reject-key", "rejected"
        )["track_id"]
        rejected = gateway.query_status(
            self.company, "demo", rejected_track, "rejected", 1, "reject-key"
        )
        unknown = gateway.send_document(
            self.company,
            "demo",
            b"synthetic XML",
            "unknown-key",
            "unknown_then_reconciled",
        )
        reconciled = gateway.reconcile_submission(
            self.company,
            "demo",
            {"idempotency_key": "unknown-key", "folio": 10, "document_code": "33"},
            "unknown_then_reconciled",
        )

        self.assertEqual(rejected["state"], "rejected")
        self.assertEqual(unknown["state"], "outcome_unknown")
        self.assertEqual(reconciled["track_id"], gateway._track_id("unknown-key"))
        self.assertTrue(reconciled["is_simulated"])

    def test_demo_factory_never_uses_live_transport(self):
        gateway = self.env["l10n_cl.sii.gateway.factory"].get_gateway("demo")

        self.assertIsInstance(gateway, DemoSiiGateway)
        with self.assertRaises(UserError):
            self.env["l10n_cl.sii.gateway.factory"].get_gateway("production")

    def test_certification_envelope_requires_verified_transport(self):
        with self.assertRaisesRegex(UserError, "No verified multi-DTE SII transport"):
            self.env["l10n_cl.sii.gateway.factory"].get_envelope_gateway(
                "certification"
            )
