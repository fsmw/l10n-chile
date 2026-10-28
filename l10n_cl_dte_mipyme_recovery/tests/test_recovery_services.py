# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
# ruff: noqa: E402

import hashlib
import os
import sys
import unittest
from datetime import date
from unittest.mock import patch

import requests

# Keep the service contract runnable without booting an Odoo database.
MODULE_ROOT = os.path.dirname(os.path.dirname(__file__))
if MODULE_ROOT not in sys.path:
    sys.path.insert(0, MODULE_ROOT)

from services.identity import (  # noqa: E402
    DteIdentity,
    foreign_identifier_key,
    normalize_folio,
    normalize_rut,
)
from services.mipyme_adapter import (
    MipymeAdapterError,
    PortalChunk,
    ProductionMipymeAdapter,
    classify_portal_response,
)  # noqa: E402
from services.recovery_service import RecoveryService  # noqa: E402
from services.xml_dte import (  # noqa: E402
    accounting_data,
    extract_candidates,
    validate_candidate,
)


class FakeCompany:
    l10n_cl_dte_service_provider = "SII"


class FakeKey:
    pem_key = b"dGVzdC1rZXk="

    def with_context(self, **kwargs):
        return self


class FakeCertificate:
    pem_certificate = b"dGVzdC1jZXJ0aWZpY2F0ZQ=="
    private_key_id = FakeKey()

    def with_context(self, **kwargs):
        return self


class FakeResponse:
    content = b"total de documentos: 1"
    text = ""

    def raise_for_status(self):
        return None


class FakeAuthSession:
    def __init__(self):
        self.cookies = requests.cookies.RequestsCookieJar()
        self.headers = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        return FakeResponse()

    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        self.cookies.set("NETSCAPE_LIVEWIRE", "authenticated")
        return FakeResponse()


class FakeAdapter:
    def __init__(self, chunks=None, error=None):
        self.chunks = chunks or []
        self.error = error

    def retrieve(self, *args):
        if self.error:
            raise self.error
        return self.chunks


class RecoveryServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(
            os.path.join(os.path.dirname(__file__), "fixtures", "mipyme_set.xml"), "rb"
        ) as fixture:
            cls.payload = fixture.read()

    def test_rut_and_folio_are_canonical(self):
        self.assertEqual(normalize_rut("76.543.210-3"), "76543210-3")
        self.assertEqual(normalize_rut("CL765432103"), "76543210-3")
        self.assertEqual(normalize_folio("00042"), 42)
        with self.assertRaises(ValueError):
            normalize_rut("76543210-7")
        with self.assertRaises(ValueError):
            normalize_folio(0)

    def test_foreign_identifier_normalization_is_country_specific(self):
        self.assertEqual(foreign_identifier_key("801223385", "PY"), "801223385")
        self.assertEqual(foreign_identifier_key(" 80122338-5 ", "PY"), "801223385")
        self.assertEqual(foreign_identifier_key("000123-4", "PY"), "0001234")
        self.assertEqual(foreign_identifier_key("12A-4", "PY"), "12A-4")
        self.assertEqual(foreign_identifier_key("12A4", "PY"), "12A4")
        self.assertEqual(foreign_identifier_key("US-12 34", "US"), "US-12 34")
        self.assertEqual(foreign_identifier_key("US1234", "US"), "US1234")

    def test_extracts_each_dte_without_utf8_rewrite(self):
        candidates = extract_candidates(self.payload)
        self.assertEqual(
            [candidate.identity.folio for candidate in candidates], [42, 43]
        )
        self.assertIn("Compañía", candidates[0].raw_xml.decode("ISO-8859-1"))
        self.assertIn(b"\xf1", candidates[0].raw_xml)
        self.assertEqual(
            candidates[0].digest, hashlib.sha256(candidates[0].raw_xml).hexdigest()
        )
        validate_candidate(candidates[0], DteIdentity("76543210-3", "33", 42))

    def test_extracts_accounting_data_for_exempt_dte(self):
        xml = b"""<?xml version="1.0" encoding="ISO-8859-1"?>
        <DTE xmlns="http://www.sii.cl/SiiDte"><Documento>
          <Encabezado><IdDoc><TipoDTE>34</TipoDTE><Folio>224</Folio>
            <FchEmis>2026-09-04</FchEmis></IdDoc>
            <Emisor><RUTEmisor>78275587-0</RUTEmisor></Emisor>
            <Receptor><RUTRecep>96961400-6</RUTRecep>
              <RznSocRecep>HARTING INTERNACIONAL LIMITADA</RznSocRecep>
              <GiroRecep>VENTA AL POR MAYOR</GiroRecep><DirRecep>AV PDTE FREI</DirRecep>
              <CmnaRecep>QUILICURA</CmnaRecep></Receptor>
            <Totales><MntExe>4571498</MntExe><MntTotal>4571498</MntTotal></Totales>
          </Encabezado><Detalle><NmbItem>FLETE INTERNACIONAL</NmbItem>
            <QtyItem>1</QtyItem><PrcItem>4571498</PrcItem><MontoItem>4571498</MontoItem>
          </Detalle><TED/><Signature/></Documento></DTE>"""
        candidate = extract_candidates(xml)[0]
        data = accounting_data(candidate)
        self.assertEqual(candidate.identity.dte_type, "34")
        self.assertEqual(candidate.identity.folio, 224)
        self.assertEqual(data.recipient_rut, "96961400-6")
        self.assertEqual(data.exempt_amount, 4571498)
        self.assertEqual(data.total_amount, 4571498)

    def test_debit_note_lines_and_reference_are_preserved(self):
        xml = b"""<DTE xmlns="http://www.sii.cl/SiiDte"><Documento>
          <Encabezado><IdDoc><TipoDTE>56</TipoDTE><Folio>225</Folio>
          <FchEmis>2026-09-04</FchEmis></IdDoc>
          <Emisor><RUTEmisor>76543210-3</RUTEmisor></Emisor>
          <Receptor><RUTRecep>96961400-6</RUTRecep></Receptor>
          <Totales><MntNeto>100</MntNeto><IVA>19</IVA>
          <MntTotal>119</MntTotal></Totales></Encabezado>
          <Detalle><NmbItem>Diferencia</NmbItem><QtyItem>2</QtyItem>
          <PrcItem>60</PrcItem><MontoItem>100</MontoItem></Detalle>
          <Referencia><TpoDocRef>33</TpoDocRef><FolioRef>12</FolioRef>
          <FchRef>2026-08-01</FchRef><CodRef>3</CodRef></Referencia>
          <TED/><Signature/></Documento></DTE>"""
        data = accounting_data(extract_candidates(xml)[0])
        self.assertEqual(data.details[0]["price_unit"], 50)
        self.assertEqual(data.references[0]["document_type"], "33")
        self.assertEqual(data.references[0]["folio"], "12")

    def test_exact_match_and_no_accounting_side_effect_boundary(self):
        result = RecoveryService(
            FakeAdapter(
                [
                    PortalChunk(date(2026, 8, 20), date(2026, 8, 20), 2, self.payload),
                ]
            )
        ).recover(
            FakeCompany(), "76543210-3", "33", 42, date(2026, 8, 1), date(2026, 8, 31)
        )
        self.assertEqual(result.status, "recovered")
        self.assertEqual(result.candidate.identity.folio, 42)

    def test_multiple_exact_matches_require_review(self):
        result = RecoveryService(
            FakeAdapter(
                [
                    PortalChunk(date(2026, 8, 20), date(2026, 8, 20), 1, self.payload),
                    PortalChunk(date(2026, 8, 21), date(2026, 8, 21), 1, self.payload),
                ]
            )
        ).recover(
            FakeCompany(), "76543210-3", "33", 42, date(2026, 8, 1), date(2026, 8, 31)
        )
        self.assertEqual(result.status, "multiple_matches")
        self.assertEqual(result.error_code, "DTE_AMBIGUO")

    def test_portal_errors_are_stable_and_safe(self):
        error = MipymeAdapterError("RESPALDO_CAPTCHA", "captcha")
        result = RecoveryService(FakeAdapter(error=error)).recover(
            FakeCompany(), "76543210-3", "33", 42, date(2026, 8, 1), date(2026, 8, 31)
        )
        self.assertEqual(result.status, "captcha")
        self.assertEqual(result.error_code, "RESPALDO_CAPTCHA")
        self.assertIsNone(result.candidate)

    def test_date_ranges_are_split_at_twenty_documents(self):
        def count(_session, _rut, _verifier, _dte_type, start, end):
            return (end - start).days + 1

        with patch.object(ProductionMipymeAdapter, "_count", side_effect=count):
            ranges = ProductionMipymeAdapter._split_ranges(
                object(),
                "76543210",
                "3",
                "33",
                date(2026, 8, 1),
                date(2026, 9, 10),
            )
        self.assertGreater(len(ranges), 1)
        self.assertTrue(all(total <= 20 for _start, _end, total in ranges))
        self.assertEqual(ranges[0][0], date(2026, 8, 22))

    def test_ambiguous_alerts_are_not_classified_as_no_data(self):
        with self.assertRaises(MipymeAdapterError) as raised:
            classify_portal_response(b"<script>alert('uno'); alert('dos');</script>")
        self.assertEqual(raised.exception.code, "RESPALDO_INDETERMINADO")

    def test_production_guard_precedes_certificate_access(self):
        class Environment:
            uid = 1

        company = FakeCompany()
        company.l10n_cl_dte_service_provider = "SIITEST"
        adapter = ProductionMipymeAdapter(Environment())
        with self.assertRaises(MipymeAdapterError) as raised:
            adapter.retrieve(
                company, "76543210-3", "33", date(2026, 8, 1), date(2026, 8, 1)
            )
        self.assertEqual(raised.exception.code, "MIPYME_PRODUCTION_ONLY")

    def test_certificate_files_are_cleaned_and_passed_as_paths(self):
        with ProductionMipymeAdapter._certificate_files(FakeCertificate()) as paths:
            self.assertEqual(len(paths), 2)
            self.assertTrue(all(os.path.exists(path) for path in paths))
            with open(paths[0], "rb") as certificate_file:
                self.assertEqual(certificate_file.read(), b"test-certificate")
            with open(paths[1], "rb") as private_key_file:
                self.assertEqual(private_key_file.read(), b"test-key")
        self.assertTrue(all(not os.path.exists(path) for path in paths))

    def test_authentication_preserves_certificate_paths_and_session_cookie(self):
        session = FakeAuthSession()
        ProductionMipymeAdapter._configure_certificate_transport(session)
        ProductionMipymeAdapter._authenticate(session, ("cert.pem", "key.pem"))
        self.assertEqual(session.cookies.get("usa_firma_central"), "true")
        self.assertTrue(session.cookies.get("NETSCAPE_LIVEWIRE"))
        self.assertEqual(session.calls[1][2]["cert"], ("cert.pem", "key.pem"))
        self.assertEqual(session.headers["Origin"], "https://www1.sii.cl")
        self.assertIn("/lista_documentos.cgi", session.headers["Referer"])

    def test_list_request_sends_lab_headers_and_emitted_flag(self):
        session = FakeAuthSession()
        ProductionMipymeAdapter._configure_certificate_transport(session)
        total = ProductionMipymeAdapter._count(
            session, "76543210", "3", "34", date(2026, 9, 1), date(2026, 9, 10)
        )
        self.assertEqual(total, 1)
        request = session.calls[-1][2]
        self.assertIn("ORIGEN=ENV", request["data"])
        self.assertEqual(request["headers"]["Origin"], "https://www1.sii.cl")
        self.assertIn("/lista_documentos.cgi", request["headers"]["Referer"])


if __name__ == "__main__":
    unittest.main()
