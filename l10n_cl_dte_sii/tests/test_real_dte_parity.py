# Copyright 2026
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Prueba de paridad contra documentos que el SII aceptó (S1).

Tres capas por documento:
1. valores por XPath (`Encabezado`, `Detalle`, `Referencia`, `Totales`);
2. TED: `DD` normalizado igual y `FRMT` byte a byte;
3. firma: el DTE reemitido debe validar con la matriz de ADR-0008.

Se ejecuta sólo con `--test-tags /l10n_cl_dte_sii:TestRealDteParity` y
`L10N_CL_REAL_DTE_DIR` definido; sin el corpus se salta sin fallar.
"""

import hashlib
import json
import os
import stat
import unittest
from pathlib import Path

from lxml import etree

from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from ..services.incoming_dte_xml import _verify_signature, validate_dte_element
from .real_dte.fixture_builder import (
    DEFAULT_CORPUS_DIR,
    REAL_DTE_DIR_ENV,
    RealDte,
    RealDteFixture,
    signed_dd_payload,
)

ALLOWLIST_PATH = (
    Path(__file__).resolve().parents[2] / "docs/parity/real-dte-allowlist.json"
)
REPORT_DIR_ENV = "L10N_CL_REAL_DTE_REPORT_DIR"
DEFAULT_REPORT_DIR = Path.home() / "l10n-cl-parity-reports"

HEADER_PATHS = [
    "Encabezado/IdDoc/TipoDTE",
    "Encabezado/IdDoc/Folio",
    "Encabezado/IdDoc/FchEmis",
    "Encabezado/IdDoc/FmaPago",
    "Encabezado/IdDoc/FchVenc",
    "Encabezado/Emisor/RUTEmisor",
    "Encabezado/Emisor/RznSoc",
    "Encabezado/Emisor/GiroEmis",
    "Encabezado/Emisor/Telefono",
    "Encabezado/Emisor/CorreoEmisor",
    "Encabezado/Emisor/Acteco",
    "Encabezado/Emisor/DirOrigen",
    "Encabezado/Emisor/CmnaOrigen",
    "Encabezado/Emisor/CdgVendedor",
    "Encabezado/Receptor/RUTRecep",
    "Encabezado/Receptor/RznSocRecep",
    "Encabezado/Receptor/GiroRecep",
    "Encabezado/Receptor/Contacto",
    "Encabezado/Receptor/CorreoRecep",
    "Encabezado/Receptor/DirRecep",
    "Encabezado/Receptor/CmnaRecep",
    "Encabezado/Totales/TpoMoneda",
    "Encabezado/Totales/MntNeto",
    "Encabezado/Totales/MntExe",
    "Encabezado/Totales/TasaIVA",
    "Encabezado/Totales/IVA",
    "Encabezado/Totales/MntTotal",
]
LINE_FIELDS = (
    "IndExe",
    "NmbItem",
    "DscItem",
    "QtyItem",
    "PrcItem",
    "DescuentoPct",
    "MontoItem",
)
LINE_NUMERIC_FIELDS = {"QtyItem", "PrcItem", "DescuentoPct", "MontoItem"}


def _xpath(path):
    return "./" + "/".join(f"*[local-name()='{step}']" for step in path.split("/"))


def _value(document, path):
    return (document.xpath(f"string({_xpath(path)})") or "").strip()


def _first_difference(expected, actual):
    """Describe el primer byte distinto entre dos payloads, para el informe."""
    for index, (left, right) in enumerate(zip(expected, actual, strict=False)):
        if left != right:
            start = max(index - 40, 0)
            return {
                "offset": index,
                "expected": expected[start : index + 40].decode("latin-1"),
                "actual": actual[start : index + 40].decode("latin-1"),
            }
    if len(expected) != len(actual):
        index = min(len(expected), len(actual))
        return {
            "offset": index,
            "expected": expected[max(index - 40, 0) :].decode("latin-1"),
            "actual": actual[max(index - 40, 0) :].decode("latin-1"),
        }
    return None


def _normalized(value):
    try:
        return f"{float(value):.6f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return value


def _lines(document):
    result = []
    for line in document.xpath("./*[local-name()='Detalle']"):
        values = []
        for name in LINE_FIELDS:
            text = (line.xpath(f"string(./*[local-name()='{name}'])") or "").strip()
            if not text:
                values.append("")
            elif name in LINE_NUMERIC_FIELDS:
                values.append(_normalized(text))
            else:
                values.append(text)
        result.append(tuple(values))
    return result


def _references(document):
    return [
        tuple(
            (node.xpath(f"string(./*[local-name()='{name}'])") or "").strip()
            for name in ("TpoDocRef", "FolioRef", "FchRef", "CodRef", "RazonRef")
        )
        for node in document.xpath("./*[local-name()='Referencia']")
    ]


@tagged("l10n_cl_real_dte", "-standard")
class TestRealDteParity(AccountTestInvoicingCommon):
    @classmethod
    @AccountTestInvoicingCommon.setup_country("cl")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.env.user.write(
            {"groups_id": [(4, cls.env.ref("l10n_cl_dte_sii.group_dte_manager").id)]}
        )
        cls.revenue_account = cls.company_data["default_account_revenue"]
        cls.results = []

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        if cls.results:
            cls._write_report(cls.results)

    @classmethod
    def _write_report(cls, results):
        directory = Path(
            os.environ.get(REPORT_DIR_ENV) or DEFAULT_REPORT_DIR
        ).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(stat.S_IRWXU)
        report = {
            "corpus": os.environ.get(REAL_DTE_DIR_ENV),
            "documents": results,
            "summary": {
                "documents": len(results),
                "values_equal": sum(1 for item in results if item["values_equal"]),
                "ted_equal": sum(1 for item in results if item["ted"]["dd_equal"]),
                "frmt_equal": sum(1 for item in results if item["ted"]["frmt_equal"]),
                "signature_ok": sum(
                    1 for item in results if item["signature"]["verified"]
                ),
            },
        }
        path = directory / "s1-real-dte-parity.json"
        path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def _allowlist(self):
        if not ALLOWLIST_PATH.is_file():
            # El allowlist vive en docs/, que no se versiona: en un clone
            # limpio no existe y la comparación es estricta.
            return {}, set()
        payload = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
        global_paths = {
            entry["path"] for entry in payload.get("global", []) if entry.get("path")
        }
        return payload.get("documents", {}), global_paths

    def _emit(self, name):
        directory = Path(os.environ.get(REAL_DTE_DIR_ENV) or DEFAULT_CORPUS_DIR)
        if not os.environ.get(REAL_DTE_DIR_ENV):
            self.skipTest(
                f"Definí {REAL_DTE_DIR_ENV} para comparar contra el corpus real."
            )
        path = directory / "emitidos" / name
        if not path.is_file():
            self.skipTest(f"No está la muestra {path}.")
        real = RealDte(path)
        fixture = RealDteFixture(self.env, real, directory / "caf")
        fixture.configure_company(self.company)
        partner = fixture.create_partner()
        document_type = self.env["l10n_latam.document.type"].search(
            [("country_id.code", "=", "CL"), ("code", "=", real.code)], limit=1
        )
        self.assertTrue(document_type, f"Falta el tipo de documento {real.code}.")
        fixture.create_caf(self.company, document_type)
        move = fixture.create_move(
            self.company, partner, document_type, self.revenue_account
        )
        certificate = fixture.certificate(self.company.partner_id.vat)
        generated = fixture.build(move, certificate)
        return real, generated

    def _compare(self, real, generated):
        documents_tolerance, global_tolerance = self._allowlist()
        tolerance = {
            entry["path"]
            for entry in documents_tolerance.get(real.path.name, [])
            if entry.get("path")
        }
        generated_document = generated.find(".//{http://www.sii.cl/SiiDte}Documento")
        differences = []
        for path in HEADER_PATHS:
            if path in tolerance or path in global_tolerance:
                continue
            expected = _value(real.document, path)
            actual = _value(generated_document, path)
            if expected != actual:
                differences.append(
                    {"path": path, "expected": expected, "actual": actual}
                )

        real_document = real.document
        if _lines(real_document) != _lines(generated_document):
            differences.append(
                {
                    "path": "Detalle",
                    "expected": _lines(real_document),
                    "actual": _lines(generated_document),
                }
            )
        if _references(real_document) != _references(generated_document):
            differences.append(
                {
                    "path": "Referencia",
                    "expected": _references(real_document),
                    "actual": _references(generated_document),
                }
            )

        self.assertIsNotNone(
            generated.find(
                ".//{http://www.sii.cl/SiiDte}TED/{http://www.sii.cl/SiiDte}DD"
            ),
            "El DTE reemitido no tiene TED/DD.",
        )
        # Se compara el DD tal como lo ve el SII dentro del documento emitido,
        # no una re-serialización aislada que agregaría namespaces heredados.
        actual_dd = signed_dd_payload(etree.tostring(generated, encoding="ISO-8859-1"))
        actual_frmt = (
            generated.findtext(
                ".//{http://www.sii.cl/SiiDte}TED/{http://www.sii.cl/SiiDte}FRMT"
            )
            or ""
        )
        signature_evidence = {}
        validate_dte_element(generated, evidence=signature_evidence)
        real_evidence = {}
        _verify_signature(real.root, evidence=real_evidence)

        result = {
            "file": real.path.name,
            "code": real.code,
            "folio": real.folio,
            "values_equal": not differences,
            "differences": differences,
            "ted": {
                "dd_equal": actual_dd == real.dd_payload,
                "dd_difference": _first_difference(real.dd_payload, actual_dd),
                "frmt_equal": actual_frmt == real.frmt,
                "frmt_expected": real.frmt,
                "frmt_actual": actual_frmt,
                "dd_sha256_expected": hashlib.sha256(real.dd_payload).hexdigest(),
                "dd_sha256_actual": hashlib.sha256(actual_dd).hexdigest(),
            },
            "signature": {
                "verified": True,
                "generated": signature_evidence,
                "real": real_evidence,
            },
        }
        self.results.append(result)
        return result

    def _assert_parity(self, name):
        real, generated = self._emit(name)
        result = self._compare(real, generated)
        self.assertFalse(
            result["differences"],
            json.dumps(result["differences"], indent=2, ensure_ascii=False),
        )
        self.assertTrue(
            result["ted"]["dd_equal"],
            "El DD reemitido no coincide: "
            + json.dumps(result["ted"]["dd_difference"], indent=2, ensure_ascii=False),
        )
        self.assertEqual(
            result["ted"]["frmt_actual"],
            result["ted"]["frmt_expected"],
            "El FRMT no coincide byte a byte.",
        )

    def test_factura_exenta_34(self):
        self._assert_parity("dte-34-factura-exenta-odoo-account.move-1095.xml")

    def test_factura_33(self):
        self._assert_parity("dte-33-factura-odoo-account.move-884.xml")

    def test_nota_credito_61(self):
        self._assert_parity("dte-61-nota-credito-odoo-account.move-718.xml")


if __name__ == "__main__":
    unittest.main()
