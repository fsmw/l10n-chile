# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.addons.l10n_cl_dte_gateway.services.sii_caf_web_client import SiiCafWebClient
from odoo.addons.l10n_cl_dte_gateway.services.sii_caf_web_types import (
    CafRequestRejected,
    HttpResponse,
)

from ..services.caf_errors import POST_DISPATCH, PRE_DISPATCH, SiiCafError
from ..services.sii_caf_client import SiiCafClient
from .common import (
    BLOCKED_PAGE,
    REJECTION_PAGE,
    SYNTHETIC_CERTIFICATE,
    SYNTHETIC_KEY,
    CafRequestTestCase,
    FakeTransport,
    allocation_pages,
    login,
    page,
    synthetic_credentials,
)


class TestSiiCafClient(CafRequestTestCase):
    def client(self, *responses, environment="certification"):
        self.transport = FakeTransport(responses)
        return SiiCafClient(
            environment,
            transport_factory=lambda: self.transport,
            credentials=synthetic_credentials,
        )

    def request_error(self, client, quantity=10, **kwargs):
        with self.assertRaises(SiiCafError) as caught:
            client.request_caf(self.company, "33", quantity, **kwargs)
        return caught.exception

    def test_success_reserves_range_before_allocation_and_logs_out(self):
        caf = self.caf_response(1, 10)
        client = self.client(*allocation_pages(1, 10), caf)
        reserved = []
        result = client.request_caf(
            self.company,
            "33",
            10,
            before_dispatch=lambda start, end: reserved.append((start, end)),
        )
        self.assertEqual(reserved, [(1, 10)])
        self.assertEqual(result.content, caf.body)
        self.assertEqual((result.folio_start, result.folio_end), (1, 10))
        calls = self.transport.calls
        self.assertTrue(calls[0]["url"].startswith("https://herculesr.sii.cl/"))
        self.assertTrue(
            all(
                call["url"].startswith("https://maullin.sii.cl/")
                for call in self.transport.assistant_calls[1:]
            )
        )
        self.assertIn(b"CANT_DOCTOS=10", calls[2]["body"])
        self.assertTrue(calls[-1]["url"].endswith("autTermino.cgi"))
        for call in calls:
            self.assertNotIn(SYNTHETIC_KEY, call["body"] or b"")
            self.assertNotIn(SYNTHETIC_CERTIFICATE, call["body"] or b"")

    def test_production_uses_palena_and_any_sii_key(self):
        caf = self.caf_response(1, 10, key_id="300")
        client = self.client(*allocation_pages(1, 10), caf, environment="production")
        self.assertEqual(
            client.endpoint, "https://palena.sii.cl/cvc_cgi/dte/of_solicita_folios"
        )
        result = client.request_caf(self.company, "33", 10)
        self.assertEqual(result.idk, "300")
        self.assertTrue(
            all(
                call["url"].startswith("https://palena.sii.cl/")
                for call in self.transport.assistant_calls[1:]
            )
        )

    def test_production_client_refuses_certification_host(self):
        web = SiiCafWebClient(transport=FakeTransport([]), environment="production")
        with self.assertRaises(CafRequestRejected):
            web._validate_assistant_url("https://maullin.sii.cl/cvc_cgi/dte/x")
        web._validate_assistant_url("https://palena.sii.cl/cvc_cgi/dte/x")

    def test_certification_requires_the_certification_key_id(self):
        caf = self.caf_response(1, 10, key_id="300")
        error = self.request_error(self.client(*allocation_pages(1, 10), caf))
        self.assertEqual(error.code, "SII_DISPATCH_UNCERTAIN")

    def test_unused_folio_block_page_is_definitive(self):
        error = self.request_error(self.client(login(), BLOCKED_PAGE))
        self.assertEqual(error.code, "SII_FOLIO_BLOCKED")
        self.assertEqual(error.phase, PRE_DISPATCH)
        self.assertFalse(error.uncertain)

    def test_sii_rejection_before_allocation(self):
        pages = allocation_pages(1, 10)
        error = self.request_error(self.client(pages[0], pages[1], REJECTION_PAGE))
        self.assertEqual(error.code, "SII_HARD_REJECTION")
        self.assertEqual(error.sii_code, "sii_rejected_request")
        self.assertEqual(error.phase, PRE_DISPATCH)

    def test_quantity_above_sii_maximum(self):
        pages = allocation_pages(1, 10, confirmation={"MAX_AUTOR": "5"})
        error = self.request_error(self.client(*pages[:3]))
        self.assertEqual(error.code, "SII_QUANTITY_LIMIT")
        self.assertEqual(error.maximum_quantity, 5)

    def test_lost_response_after_allocation_is_uncertain_with_range(self):
        pages = allocation_pages(1, 10)
        client = self.client(*pages[:3], RuntimeError("synthetic lost response"))
        reserved = []
        error = self.request_error(
            client, before_dispatch=lambda start, end: reserved.append((start, end))
        )
        self.assertEqual(reserved, [(1, 10)])
        self.assertEqual(error.code, "SII_DISPATCH_UNCERTAIN")
        self.assertEqual(error.phase, POST_DISPATCH)
        self.assertTrue(error.uncertain)
        self.assertEqual((error.folio_start, error.folio_end), (1, 10))

    def test_malformed_caf_download_is_uncertain(self):
        broken = HttpResponse(200, {}, b"<AUTORIZACION><CAF/></AUTORIZACION>")
        error = self.request_error(self.client(*allocation_pages(1, 10), broken))
        self.assertEqual(error.code, "SII_DISPATCH_UNCERTAIN")
        self.assertEqual(error.sii_code, "caf_download_or_validation_uncertain")

    def test_login_failures_are_pre_dispatch(self):
        error = self.request_error(self.client(page("<html>Acceso</html>")))
        self.assertEqual(error.code, "SII_AUTHENTICATION_FAILED")
        error = self.request_error(self.client(RuntimeError("synthetic refused")))
        self.assertEqual(error.code, "SII_TRANSPORT_ERROR")
        self.assertEqual(error.phase, PRE_DISPATCH)

    def test_missing_certificate_is_reported_without_network(self):
        transport = FakeTransport([])
        client = SiiCafClient("certification", transport_factory=lambda: transport)
        with self.assertRaises(SiiCafError) as caught:
            client.request_caf(self.company, "33", 10)
        self.assertEqual(caught.exception.code, "SII_CERTIFICATE_INVALID")
        self.assertFalse(transport.calls)

    def test_recovery_downloads_only_the_authorized_range(self):
        client = self.client(*self.recovery_pages(5, 9))
        result = client.recover_caf(self.company, "33", 5, 9)
        self.assertEqual((result.folio_start, result.folio_end), (5, 9))
        urls = [call["url"] for call in self.transport.assistant_calls[1:]]
        self.assertTrue(all("/rf_" in url for url in urls))
        self.assertFalse(any("of_genera_folio" in url for url in urls))

    def test_input_validation(self):
        client = self.client()
        with self.assertRaises(SiiCafError) as caught:
            client.request_caf(self.company, "99", 10)
        self.assertEqual(caught.exception.code, "SII_DOCUMENT_TYPE_INVALID")
        with self.assertRaises(SiiCafError) as caught:
            client.request_caf(self.company, "33", 0)
        self.assertEqual(caught.exception.code, "SII_QUANTITY_INVALID")
        with self.assertRaises(SiiCafError) as caught:
            SiiCafClient("demo")
        self.assertEqual(caught.exception.code, "SII_ENVIRONMENT_INVALID")
        self.assertFalse(self.transport.calls)
