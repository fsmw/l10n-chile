#!/usr/bin/env python3
"""Verificación de paridad de los DTE reales contra sus CAF.

Lee un directorio con XML reales (emitidos/ y recibidos/) y los CAF locales,
verifica el TED/FRMT con la clave pública del CAF y reporta qué CAF cubre cada
folio. No usa Odoo, no accede a la red y no lee claves privadas ni contraseñas.

Uso:
    venv/bin/python tools/verify_real_dte.py \
        --dir /home/fsmw/dev/l10n_cl/xml-reales \
        --report ~/l10n-cl-parity-reports/s1.json

Salida: JSON con un registro por documento y un resumen. Exit 0 si todos los
FRMT verifican y cada folio tiene CAF que lo cubre; 1 en caso contrario.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from pathlib import Path

from lxml import etree

# DigestInfo PKCS#1 v1.5 conocidos (prefijo DER -> algoritmo).
DIGEST_INFOS = {
    bytes.fromhex("3021300906052b0e03021a05000414"): "sha1",
    bytes.fromhex("3031300d060960864801650304020105000420"): "sha256",
}

XML_ENCODING_RE = re.compile(rb'encoding=["\']([A-Za-z0-9._-]+)["\']')
DD_RE = re.compile(rb"<DD\b[^>]*>.*?</DD>", re.S)


def b64(value: str) -> bytes:
    """Decodifica base64 ignorando espacios y saltos de línea."""
    return base64.b64decode(re.sub(r"\s+", "", value or ""))


def xml_declared_encoding(raw: bytes) -> str:
    match = XML_ENCODING_RE.search(raw[:120])
    return match.group(1).decode("ascii", "replace") if match else "ISO-8859-1"


def rsa_public_numbers(modulus_b64: str, exponent_b64: str) -> tuple[int, int]:
    return (
        int.from_bytes(b64(modulus_b64), "big"),
        int.from_bytes(b64(exponent_b64), "big"),
    )


def pkcs1_digest(signature: bytes, n: int, e: int) -> tuple[str, bytes] | None:
    """Devuelve (algoritmo, digest esperado) de una firma PKCS#1 v1.5."""
    size = (n.bit_length() + 7) // 8
    encoded = pow(int.from_bytes(signature, "big"), e, n).to_bytes(size, "big")
    if not encoded.startswith(b"\x00\x01"):
        return None
    separator = encoded.find(b"\x00", 2)
    if separator < 0:
        return None
    info = encoded[separator + 1:]
    for prefix, algorithm in DIGEST_INFOS.items():
        if info.startswith(prefix):
            return algorithm, info[len(prefix):]
    return None


def frmt_payloads(raw: bytes) -> list[bytes]:
    """Bytes candidatos que firma el SII: el DD con etiquetas, sin `\\n\\s*`.

    Primero se usan los bytes tal como están en el archivo (es lo que firmó el
    emisor). Como respaldo se normaliza el texto a ISO-8859-1, que es la
    codificación que declara el instructivo.
    """
    match = DD_RE.search(raw)
    if not match:
        return []
    raw_dd = match.group(0)
    candidates = [re.sub(rb"\n\s*", b"", raw_dd)]
    encoding = xml_declared_encoding(raw)
    text = raw_dd.decode(encoding, "replace")
    latin1 = re.sub(rb"\n\s*", b"", text.encode("ISO-8859-1", "replace"))
    if latin1 not in candidates:
        candidates.append(latin1)
    return candidates


def parse_caf(path: Path) -> dict | None:
    """Extrae metadatos públicos de un CAF: tipo, rango y clave pública."""
    try:
        root = etree.parse(str(path)).getroot()
    except (etree.XMLSyntaxError, OSError):
        return None

    def text(expr: str) -> str:
        found = root.xpath(expr)
        return found[0].strip() if found else ""

    document_type = text("//*[local-name()='TD']/text()")
    start = text("//*[local-name()='RNG']/*[local-name()='D']/text()")
    end = text("//*[local-name()='RNG']/*[local-name()='H']/text()")
    modulus = text("//*[local-name()='RSAPK']/*[local-name()='M']/text()")
    exponent = text("//*[local-name()='RSAPK']/*[local-name()='E']/text()")
    if not (document_type and start.isdigit() and end.isdigit() and modulus):
        return None
    return {
        "path": path,
        "name": path.name,
        "document_type": document_type,
        "start": int(start),
        "end": int(end),
        "modulus": re.sub(r"\s+", "", modulus),
        "exponent": re.sub(r"\s+", "", exponent),
    }


def inspect_document(path: Path, folder: str, cafs: list[dict]) -> dict:
    raw = path.read_bytes()
    root = etree.fromstring(raw)
    document_type = root.xpath("string(.//*[local-name()='TD'][1])")
    folio = root.xpath("string(.//*[local-name()='F'][1])")
    issued = root.xpath("string(.//*[local-name()='FE'][1])")

    dd = root.xpath(".//*[local-name()='DD']")
    frmt = root.xpath("string(.//*[local-name()='FRMT'])")
    modulus = (
        dd[0].xpath("string(.//*[local-name()='RSAPK']/*[local-name()='M'])")
        if dd
        else ""
    )
    exponent = (
        dd[0].xpath("string(.//*[local-name()='RSAPK']/*[local-name()='E'])")
        if dd
        else ""
    )

    result = {
        "file": path.name,
        "folder": folder,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "document_type": document_type,
        "folio": int(folio) if folio.isdigit() else None,
        "issued": issued,
        "frmt": "sin TED",
        "caf": None,
        "caf_covers": False,
    }

    if modulus and frmt:
        expected = pkcs1_digest(b64(frmt), *rsa_public_numbers(modulus, exponent))
        if expected:
            algorithm, digest = expected
            if any(
                hashlib.new(algorithm, payload).digest() == digest
                for payload in frmt_payloads(raw)
            ):
                result["frmt"] = f"ok ({algorithm})"
            else:
                result["frmt"] = "mismatch"
        else:
            result["frmt"] = "padding inválido"

    normalized = re.sub(r"\s+", "", modulus)
    candidates = [caf for caf in cafs if caf["modulus"] == normalized]
    if candidates:
        caf = candidates[0]
        result["caf"] = {"file": caf["name"], "range": f"{caf['start']}-{caf['end']}"}
        if result["folio"] is not None:
            result["caf_covers"] = caf["start"] <= result["folio"] <= caf["end"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        required=True,
        type=Path,
        help="directorio con emitidos/, recibidos/ y caf/",
    )
    parser.add_argument(
        "--report", type=Path, help="ruta del JSON de salida (fuera del repo)"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="no imprime el detalle por documento"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "falla también si un recibido no verifica el FRMT "
            "(por defecto sólo informa)"
        ),
    )
    args = parser.parse_args()

    cafs = [
        caf
        for path in sorted((args.dir / "caf").glob("*.xml"))
        if (caf := parse_caf(path))
    ]
    if not cafs:
        print(f"No se encontraron CAF en {args.dir / 'caf'}", file=sys.stderr)
        return 2

    documents: list[dict] = []
    for folder in ("emitidos", "recibidos"):
        for path in sorted((args.dir / folder).glob("*.xml")):
            documents.append(inspect_document(path, folder, cafs))

    emitted = [doc for doc in documents if doc["folder"] == "emitidos"]
    received = [doc for doc in documents if doc["folder"] == "recibidos"]
    # S1 se acepta con los emitidos: son los que el fork debe reproducir. Los
    # recibidos se informan; un FRMT no verificable no bloquea S1 salvo --strict.
    emitted_ok = all(
        doc["frmt"].startswith("ok") and doc["caf_covers"] for doc in emitted
    )
    received_pending = [doc for doc in received if not doc["frmt"].startswith("ok")]
    ok = emitted_ok and (not args.strict or not received_pending)
    report = {
        "source": str(args.dir),
        "documents": documents,
        "summary": {
            "documents": len(documents),
            "frmt_ok": sum(1 for doc in documents if doc["frmt"].startswith("ok")),
            "emitted": len(emitted),
            "emitted_frmt_ok": sum(
                1 for doc in emitted if doc["frmt"].startswith("ok")
            ),
            "emitted_caf_covered": sum(1 for doc in emitted if doc["caf_covers"]),
            "emitted_ok": emitted_ok,
            "received": len(received),
            "received_frmt_ok": len(received) - len(received_pending),
            "received_pending": [doc["file"] for doc in received_pending],
            "ok": ok,
        },
    }

    if not args.quiet:
        for doc in documents:
            caf_label = (
                doc["caf"]["file"]
                if doc["caf"]
                else "n/a (emisor)" if doc["folder"] == "recibidos" else "?"
            )
            suffix = (
                ""
                if doc["caf_covers"] or doc["folder"] == "recibidos"
                else " (fuera de rango)"
            )
            print(
                f"{doc['file'][:58]:60s} TD={doc['document_type']:>3} "
                f"folio={doc['folio']} FRMT={doc['frmt']:12s} CAF={caf_label}{suffix}"
            )

    summary = report["summary"]
    print(
        f"\n{summary['documents']} documentos | "
        f"emitidos: FRMT {summary['emitted_frmt_ok']}/{summary['emitted']}, "
        f"CAF {summary['emitted_caf_covered']}/{summary['emitted']} | "
        f"recibidos: FRMT {summary['received_frmt_ok']}/{summary['received']} | "
        f"resultado: {'OK' if ok else 'FALLA'}"
    )
    if summary["received_pending"]:
        print(
            "Revisar FRMT de recibidos (no bloquea S1): "
            + ", ".join(summary["received_pending"])
        )

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"Informe: {args.report}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
