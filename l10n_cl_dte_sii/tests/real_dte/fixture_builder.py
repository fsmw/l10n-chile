# Copyright 2026
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Reconstrucción de registros Odoo a partir de un DTE real del SII (S1).

El corpus de `xml-reales/` es de sólo lectura y nunca se copia al repositorio.
Este módulo lee un documento emitido y crea la compañía, el receptor, los
productos, las líneas, las referencias y el CAF que lo cubre, de modo que el
`DteXmlBuilder` del fork pueda reemitirlo y compararlo con el original.

Reglas:
- El CAF y su clave privada se cargan en memoria desde `xml-reales/caf/`; no se
  versionan ni se imprimen.
- El folio se fuerza al de la muestra únicamente en la fixture.
- `TSTED`/`TmstFirma` se congelan con el valor del XML para que el TED sea
  reproducible byte a byte.
"""

import base64
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from lxml import etree

from odoo.addons.l10n_cl_dte_sii.services.dte_xml import DteXmlBuilder

DTE_NS = "http://www.sii.cl/SiiDte"
SII_TAXPAYER_TYPE_COMPANY = "1"
REAL_DTE_DIR_ENV = "L10N_CL_REAL_DTE_DIR"
DEFAULT_CORPUS_DIR = Path("/home/fsmw/dev/l10n_cl/xml-reales")
DD_PATTERN = re.compile(rb"<DD\b[^>]*>.*?</DD>", re.S)


def _text(node, path):
    return (node.xpath(f"string({path})") or "").strip()


def _local(document, name):
    return document.xpath(f"./*[local-name()='{name}']")


def strip_dte_namespace(payload):
    """Quita la declaración del namespace SII de la etiqueta raíz."""
    opening_end = payload.find(b">")
    declaration = f' xmlns="{DTE_NS}"'.encode()
    if opening_end < 0:
        return payload
    opening = payload[:opening_end]
    return opening.replace(declaration, b"", 1) + payload[opening_end:]


def signed_dd_payload(raw_xml):
    """Bytes que firma el SII: el `DD` con etiquetas y `\\n\\s*` eliminado."""
    match = DD_PATTERN.search(raw_xml)
    if not match:
        raise ValueError("El XML real no contiene un bloque DD.")
    return re.sub(rb"\n\s*", b"", match.group(0))


class RealDte:
    """Vista de sólo lectura sobre un DTE emitido del corpus."""

    def __init__(self, path):
        self.path = Path(path)
        self.raw = self.path.read_bytes()
        self.root = etree.fromstring(self.raw)
        documents = self.root.xpath(".//*[local-name()='Documento']")
        if len(documents) != 1:
            raise ValueError(f"{self.path.name} no tiene un único Documento.")
        self.document = documents[0]

    # Identidad del documento
    @property
    def code(self):
        return _text(
            self.document,
            "./*[local-name()='Encabezado']"
            "/*[local-name()='IdDoc']/*[local-name()='TipoDTE']",
        )

    @property
    def folio(self):
        return int(
            _text(
                self.document,
                "./*[local-name()='Encabezado']/*[local-name()='IdDoc']"
                "/*[local-name()='Folio']",
            )
        )

    @property
    def issued(self):
        return _text(
            self.document,
            "./*[local-name()='Encabezado']/*[local-name()='IdDoc']"
            "/*[local-name()='FchEmis']",
        )

    @property
    def due(self):
        return (
            _text(
                self.document,
                "./*[local-name()='Encabezado']/*[local-name()='IdDoc']"
                "/*[local-name()='FchVenc']",
            )
            or self.issued
        )

    @property
    def stamp(self):
        return _text(self.document, ".//*[local-name()='DD']/*[local-name()='TSTED']")

    @property
    def frmt(self):
        return _text(self.document, ".//*[local-name()='FRMT']")

    @property
    def dd_payload(self):
        return signed_dd_payload(self.raw)

    # Emisor, receptor y totales
    def header_value(self, group, name):
        return _text(
            self.document,
            f"./*[local-name()='Encabezado']/*[local-name()='{group}']"
            f"/*[local-name()='{name}']",
        )

    def total_value(self, name):
        return _text(
            self.document,
            f"./*[local-name()='Encabezado']/*[local-name()='Totales']"
            f"/*[local-name()='{name}']",
        )

    @property
    def lines(self):
        return [
            {
                "number": int(_text(line, "./*[local-name()='NroLinDet']") or 0),
                "exempt": bool(_local(line, "IndExe")),
                "name": _text(line, "./*[local-name()='NmbItem']"),
                "description": _text(line, "./*[local-name()='DscItem']"),
                "quantity": float(_text(line, "./*[local-name()='QtyItem']") or 1),
                "price": float(_text(line, "./*[local-name()='PrcItem']") or 0),
                "discount": float(_text(line, "./*[local-name()='DescuentoPct']") or 0),
                "amount": _text(line, "./*[local-name()='MontoItem']"),
            }
            for line in _local(self.document, "Detalle")
        ]

    @property
    def references(self):
        return [
            {
                "doc_type": _text(node, "./*[local-name()='TpoDocRef']"),
                "folio": _text(node, "./*[local-name()='FolioRef']"),
                "date": _text(node, "./*[local-name()='FchRef']"),
                "code": _text(node, "./*[local-name()='CodRef']"),
                "reason": _text(node, "./*[local-name()='RazonRef']"),
            }
            for node in _local(self.document, "Referencia")
        ]

    @property
    def caf_modulus(self):
        return _text(
            self.document,
            ".//*[local-name()='DD']/*[local-name()='CAF']/*[local-name()='DA']"
            "/*[local-name()='RSAPK']/*[local-name()='M']",
        )


class RealDteFixture:
    """Crea en Odoo los registros que reproducen el documento real."""

    def __init__(self, env, real_dte, caf_directory=None):
        self.env = env
        self.real = real_dte
        self.caf_directory = Path(
            caf_directory or (Path(real_dte.path).parents[1] / "caf")
        )
        self.caf_root = None
        self.builder_arguments = None

    # ------------------------------------------------------------------
    # Compañía y receptor
    # ------------------------------------------------------------------

    def configure_company(self, company):
        partner = company.partner_id
        partner.write(
            {
                "name": self.real.header_value("Emisor", "RznSoc"),
                "vat": self.real.header_value("Emisor", "RUTEmisor"),
                "street": self.real.header_value("Emisor", "DirOrigen"),
                "city": self.real.header_value("Emisor", "CmnaOrigen"),
                "phone": self.real.header_value("Emisor", "Telefono") or False,
                "email": self.real.header_value("Emisor", "CorreoEmisor") or False,
            }
        )
        company.write(
            {
                "name": partner.name,
                "l10n_cl_dte_activity": self.real.header_value("Emisor", "GiroEmis"),
                "l10n_cl_dte_activity_code": self.real.header_value("Emisor", "Acteco"),
                "l10n_cl_dte_email": self.real.header_value("Emisor", "CorreoEmisor")
                or False,
                "l10n_cl_dte_resolution_number": "0",
                "l10n_cl_dte_resolution_date": date(2020, 1, 1),
                "currency_id": self.env.ref("base.CLP").id,
                "tax_calculation_rounding_method": "round_per_line",
            }
        )
        return company

    def create_partner(self):
        receiver_rut = self.real.header_value("Receptor", "RUTRecep")
        return self.env["res.partner"].create(
            {
                "name": self.real.header_value("Receptor", "RznSocRecep"),
                "vat": receiver_rut,
                "country_id": self.env.ref("base.cl").id,
                "street": self.real.header_value("Receptor", "DirRecep"),
                "city": self.real.header_value("Receptor", "CmnaRecep"),
                "phone": self.real.header_value("Receptor", "Contacto") or False,
                "email": self.real.header_value("Receptor", "CorreoRecep") or False,
                "l10n_cl_dte_email": self.real.header_value("Receptor", "CorreoRecep")
                or False,
                "l10n_cl_activity_description": self.real.header_value(
                    "Receptor", "GiroRecep"
                ),
                "l10n_latam_identification_type_id": self.env.ref(
                    "l10n_latam_base.it_vat"
                ).id,
                "l10n_cl_sii_taxpayer_type": SII_TAXPAYER_TYPE_COMPANY,
            }
        )

    # ------------------------------------------------------------------
    # CAF
    # ------------------------------------------------------------------

    def find_caf(self):
        """Devuelve el CAF cuyo `RSAPK/M` firma el TED de la muestra."""
        expected = re.sub(r"\s+", "", self.real.caf_modulus)
        for path in sorted(self.caf_directory.glob("*.xml")):
            root = etree.fromstring(path.read_bytes())
            modulus = _text(root, "//*[local-name()='RSAPK']/*[local-name()='M']")
            if re.sub(r"\s+", "", modulus) != expected:
                continue
            document_type = _text(root, "//*[local-name()='TD']")
            start = _text(root, "//*[local-name()='RNG']/*[local-name()='D']")
            end = _text(root, "//*[local-name()='RNG']/*[local-name()='H']")
            if (
                document_type == self.real.code
                and start.isdigit()
                and end.isdigit()
                and int(start) <= self.real.folio <= int(end)
            ):
                return path
        raise ValueError(
            f"Ningún CAF de {self.caf_directory} cubre el folio "
            f"{self.real.folio} del tipo {self.real.code}."
        )

    def create_caf(self, company, document_type):
        path = self.find_caf()
        caf = self.env["l10n_cl.dte.caf"].create_from_xml(
            path.name, path.read_bytes(), company
        )
        caf.action_enable()
        # Sólo en la fixture: el folio de la muestra se reserva a mano.
        caf.write({"next_folio": self.real.folio})
        self.caf_root = caf._decode_caf()
        return caf

    # ------------------------------------------------------------------
    # Factura
    # ------------------------------------------------------------------

    def _tax(self, company, name):
        return self.env["account.tax"].search(
            [
                ("name", "=", name),
                ("type_tax_use", "=", "sale"),
                ("company_id", "=", company.id),
            ],
            limit=1,
        )

    def _unit(self):
        return self.env.ref("uom.product_uom_unit")

    def _seller_user(self, company):
        name = self.real.header_value("Emisor", "CdgVendedor")
        if not name:
            return self.env["res.users"]
        return self.env["res.users"].create(
            {
                "name": name,
                "login": f"parity-seller-{self.real.code}-{self.real.folio}",
                "company_id": company.id,
                "company_ids": [(6, 0, [company.id])],
            }
        )

    def _line_values(self, company, line, revenue_account, vat):
        product = self.env["product.product"].create(
            {
                "name": line["name"],
                "company_id": company.id,
                "default_code": False,
                "type": "service",
                "list_price": line["price"],
                "uom_id": self._unit().id,
                "uom_po_id": self._unit().id,
            }
        )
        return {
            "name": line["description"] or line["name"],
            "product_id": product.id,
            "product_uom_id": self._unit().id,
            "quantity": line["quantity"],
            "price_unit": line["price"],
            "discount": line["discount"],
            "account_id": revenue_account.id,
            "tax_ids": [(5, 0, 0)] if line["exempt"] else [(6, 0, [vat.id])],
        }

    def create_move(self, company, partner, document_type, revenue_account):
        code = self.real.code
        is_credit_note = code in {"61", "56", "111", "112"}
        move_type = "out_refund" if is_credit_note else "out_invoice"
        vat = self._tax(company, "19% VAT")
        if not any(line["exempt"] for line in self.real.lines):
            assert vat, "Falta el impuesto 19% VAT de la localización chilena."
        move = self.env["account.move"].create(
            {
                "move_type": move_type,
                "company_id": company.id,
                "journal_id": self.env["account.journal"]
                .search(
                    [
                        ("company_id", "=", company.id),
                        ("type", "=", "sale"),
                    ],
                    limit=1,
                )
                .id,
                "partner_id": partner.id,
                "currency_id": self.env.ref("base.CLP").id,
                "invoice_date": self.real.issued,
                "invoice_date_due": self.real.due,
                "l10n_latam_document_type_id": document_type.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        self._line_values(company, line, revenue_account, vat),
                    )
                    for line in self.real.lines
                ],
            }
        )
        seller = self._seller_user(company)
        if seller:
            move.write({"invoice_user_id": seller.id})
        if self.real.references:
            move.write(
                {
                    "l10n_cl_reference_ids": [
                        (
                            0,
                            0,
                            {
                                "l10n_cl_reference_doc_type_id": self.env[
                                    "l10n_latam.document.type"
                                ]
                                .search(
                                    [
                                        ("country_id.code", "=", "CL"),
                                        ("code", "=", reference["doc_type"]),
                                    ],
                                    limit=1,
                                )
                                .id,
                                "origin_doc_number": reference["folio"],
                                "date": reference["date"] or False,
                                "reference_doc_code": reference["code"] or False,
                                "reason": reference["reason"] or False,
                            },
                        )
                        for reference in self.real.references
                    ]
                }
            )
        return move

    # ------------------------------------------------------------------
    # Firma sintética y emisión
    # ------------------------------------------------------------------

    @staticmethod
    def certificate(subject_rut):
        """Certificado autofirmado real: el validador debe poder parsearlo."""
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, "Parity fixture"),
                x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, subject_rut),
            ]
        )
        valid_from = datetime(2025, 1, 1, tzinfo=timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(valid_from)
            .not_valid_after(valid_from + timedelta(days=365 * 5))
            .sign(key, hashes.SHA256())
        )
        der = certificate.public_bytes(serialization.Encoding.DER)
        numbers = key.public_key().public_numbers()

        class FixtureCertificate:
            subject_serial_number = subject_rut

            def _sign(self, data, hashing_algorithm, formatting):
                return base64.b64encode(
                    key.sign(data, padding.PKCS1v15(), hashes.SHA1())
                )

            def _get_public_key_numbers_bytes(self, formatting):
                return (
                    base64.b64encode(
                        numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
                    ),
                    base64.b64encode(
                        numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
                    ),
                )

            def _get_der_certificate_bytes(self, formatting):
                return base64.b64encode(der)

        return FixtureCertificate()

    def build(self, move, certificate):
        """Reemite el DTE con el folio y el sello del documento real."""
        if self.caf_root is None:
            raise ValueError("Cargá el CAF antes de construir el DTE.")
        with patch.object(DteXmlBuilder, "_timestamp", return_value=self.real.stamp):
            builder = DteXmlBuilder(move, self.real.folio, self.caf_root, certificate)
            return builder.build()
