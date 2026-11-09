# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import sys
from unittest.mock import patch

from odoo.tests import TransactionCase

from ..services.sii_certification_caf_client import (
    CafDispatchUncertain,
    CafRequestRejected,
    CafSessionExpired,
    PycurlTransport,
    SiiCertificationCafClient,
)


class _FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers, body=None, certificate=None, key=None):
        self.calls.append((method, url, headers, body, certificate, key))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _FakePycurl:
    URL = 1
    CUSTOMREQUEST = 2
    FOLLOWLOCATION = 3
    CONNECTTIMEOUT = 4
    TIMEOUT = 5
    SSL_VERIFYPEER = 6
    SSL_VERIFYHOST = 7
    HTTPHEADER = 8
    HEADERFUNCTION = 9
    WRITEFUNCTION = 10
    ACCEPT_ENCODING = 11
    POSTFIELDS = 12
    SSLCERT_BLOB = 13
    SSLCERTTYPE = 14
    SSLKEY_BLOB = 15
    SSLKEYTYPE = 16
    PROTOCOLS = 17
    PROTO_HTTPS = 2
    RESPONSE_CODE = 18

    def __init__(self):
        self.snapshots = []

    @staticmethod
    def version_info():
        return (11, "8.0.0", 0x080000, "linux", 0, "OpenSSL/3.0")

    def Curl(self):
        return _FakeCurl(self)


class _FakeCurl:
    def __init__(self, module):
        self.module = module
        self.options = {}

    def reset(self):
        self.options = {}

    def setopt(self, option, value):
        self.options[option] = value

    def perform(self):
        self.module.snapshots.append(dict(self.options))
        self.options[self.module.HEADERFUNCTION](b"HTTP/1.1 200 OK\r\n")
        self.options[self.module.HEADERFUNCTION](b"Set-Cookie: TOKEN=one\r\n")
        self.options[self.module.WRITEFUNCTION](b"body")

    def getinfo(self, _option):
        return 200

    def close(self):
        return None


def _html_form(action, values):
    controls = "".join(
        f'<input type="hidden" name="{name}" value="{value}">'
        for name, value in values.items()
    )
    return f'<html><form action="{action}">{controls}</form></html>'


def _caf_xml(code="33", rut="12345678-5", start=501, end=502, idk=100):
    return (
        "<?xml version='1.0' encoding='ISO-8859-1'?>"
        f"<AUTORIZACION><CAF><DA><RE>{rut}</RE><TD>{code}</TD>"
        f"<RNG><D>{start}</D><H>{end}</H></RNG><FA>2026-01-01</FA>"
        f"<IDK>{idk}</IDK></DA></CAF></AUTORIZACION>"
    ).encode("iso-8859-1")


def _success_responses():
    from ..services.sii_certification_caf_client import HttpResponse

    return [
        HttpResponse(200, {}, b'<script>document.cookie="TOKEN=abc; path=/"</script>'),
        HttpResponse(
            200,
            {},
            _html_form("of_solicita_folios_dcto", {"hidden": "one"}).encode(),
        ),
        HttpResponse(
            200,
            {},
            _html_form(
                "of_genera_folio",
                {"FOLIO_INI": "501", "FOLIO_FIN": "502", "NOMUSU": "user"},
            ).encode(),
        ),
        HttpResponse(
            200,
            {},
            _html_form("of_genera_archivo", {"hidden": "two"}).encode(),
        ),
        HttpResponse(200, {}, _caf_xml()),
    ]


class TestCertificationCafClient(TransactionCase):
    """Test SII assistant boundaries without making network calls."""

    def test_success_uses_reserved_range_and_validates_returned_caf(self):
        transport = _FakeTransport(_success_responses())
        client = SiiCertificationCafClient(transport=transport)
        session = client.authenticate(b"cert", b"key")
        ranges = []

        result = client.request_caf(
            session,
            "12.345.678-5",
            "33",
            2,
            before_dispatch=lambda start, end: ranges.append((start, end)),
        )

        self.assertEqual(ranges, [(501, 502)])
        self.assertEqual(result.idk, "100")
        self.assertEqual(result.document_code, "33")
        self.assertEqual((result.folio_start, result.folio_end), (501, 502))
        self.assertEqual(len(transport.calls), 5)
        self.assertTrue(all(call[1].startswith("https://") for call in transport.calls))

    def test_external_redirect_is_rejected_before_following_it(self):
        from ..services.sii_certification_caf_client import HttpResponse

        transport = _FakeTransport(
            [HttpResponse(302, {"location": ["https://evil.example/steal"]}, b"")]
        )
        client = SiiCertificationCafClient(transport=transport)

        with self.assertRaises(CafRequestRejected):
            client.authenticate(b"cert", b"key")

        self.assertEqual(len(transport.calls), 1)

    def test_session_expiration_before_dispatch_is_safe(self):
        from ..services.sii_certification_caf_client import HttpResponse

        transport = _FakeTransport(
            [
                HttpResponse(
                    200,
                    {},
                    b'<script>document.cookie="TOKEN=abc; path=/"</script>',
                ),
                HttpResponse(200, {}, b"IngresoCertificado"),
            ]
        )
        client = SiiCertificationCafClient(transport=transport)
        session = client.authenticate(b"cert", b"key")

        with self.assertRaises(CafSessionExpired):
            client.prepare_request(session, "12345678-5", "33", 2)

        self.assertEqual(len(transport.calls), 2)

    def test_transfer_failure_after_range_persistence_is_uncertain(self):
        responses = _success_responses()
        responses[3] = RuntimeError("simulated lost HTTP response")
        transport = _FakeTransport(responses)
        client = SiiCertificationCafClient(transport=transport)
        session = client.authenticate(b"cert", b"key")
        prepared = client.prepare_request(session, "12345678-5", "33", 2)
        persisted = []

        with self.assertRaises(CafDispatchUncertain) as error:
            client.dispatch_and_download(
                session,
                prepared,
                "12345678-5",
                "33",
                before_dispatch=lambda start, end: persisted.append((start, end)),
            )

        self.assertEqual(persisted, [(501, 502)])
        self.assertEqual(
            (error.exception.folio_start, error.exception.folio_end), (501, 502)
        )
        self.assertEqual(error.exception.code, "caf_download_or_validation_uncertain")

    def test_caf_environment_company_type_and_range_are_all_checked(self):
        from ..services.sii_certification_caf_client import HttpResponse, _parse_caf

        for xml in (
            _caf_xml(idk=1),
            _caf_xml(rut="87654321-0"),
            _caf_xml(code="34"),
            _caf_xml(start=500, end=501),
        ):
            transport = _FakeTransport(_success_responses())
            client = SiiCertificationCafClient(transport=transport)
            session = client.authenticate(b"cert", b"key")
            prepared = client.prepare_request(session, "12345678-5", "33", 2)
            transport.responses[1] = HttpResponse(200, {}, xml)
            with self.assertRaises(CafDispatchUncertain):
                client.dispatch_and_download(
                    session,
                    prepared,
                    "12345678-5",
                    "33",
                    before_dispatch=lambda _start, _end: None,
                )

        with self.assertRaises(CafRequestRejected):
            _parse_caf(b"<!DOCTYPE x [<!ENTITY e SYSTEM 'file:///etc/passwd'>]><x/>")

    def test_pycurl_passes_certificate_and_key_as_in_memory_blobs(self):
        module = _FakePycurl()
        with patch.dict(sys.modules, {"pycurl": module}):
            transport = PycurlTransport()
            response = transport.request(
                "POST",
                "https://maullin.sii.cl/",
                {"Content-Type": "application/x-www-form-urlencoded"},
                body=b"x=1",
                certificate=b"pem-chain-bytes",
                key=b"private-key-bytes",
            )
            options = module.snapshots[0]
            transport.close()

        self.assertEqual(response.body, b"body")
        self.assertEqual(options[module.SSLCERT_BLOB], b"pem-chain-bytes")
        self.assertEqual(options[module.SSLKEY_BLOB], b"private-key-bytes")
        self.assertEqual(options[module.SSLCERTTYPE], "PEM")
        self.assertEqual(options[module.SSLKEYTYPE], "PEM")
        self.assertFalse(any("FILE" in str(option) for option in options))
