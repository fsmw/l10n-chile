# Copyright 2026
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Matriz XMLDSig de recepción (ADR-0008).

Los doce DTE reales de `xml-reales/` verifican con C14N **exclusivo**, aunque
declaren `CanonicalizationMethod` inclusivo. El digest del `Documento` verifica
en exclusivo con namespace o, si falla, sin `xmlns="http://www.sii.cl/SiiDte"`.
Este módulo reproduce cada variante con firmas sintéticas y exige que toda
alteración de contenido, `SignedInfo` o namespace sea rechazada.
"""

import base64
import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from lxml import etree

from odoo.tests import TransactionCase, tagged

from ..services.incoming_dte_xml import (
    DSIG_NS,
    DTE_C14N,
    DTE_NS,
    ENVELOPED_SIGNATURE,
    DteXmlValidationError,
    _canonical,
    _exclusive_canonical,
    _verify_signature,
    _without_dte_namespace,
    validate_dte_element,
)

REAL_DTE_DIR_ENV = "L10N_CL_REAL_DTE_DIR"
DIGEST_VARIANTS = {
    "exclusivo": lambda node: _exclusive_canonical(node),
    "exclusivo-sin-namespace": lambda node: _without_dte_namespace(
        _exclusive_canonical(node)
    ),
    "inclusivo": lambda node: _canonical(node),
}
SIGNATURE_VARIANTS = {
    "exclusivo": lambda node: _exclusive_canonical(node),
    "inclusivo": lambda node: _canonical(node),
}


@tagged("post_install", "-at_install")
class TestXmldsigVariants(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "Firma SII sintética")]
        )
        cls.certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(cls.private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
            .sign(cls.private_key, hashes.SHA256())
        )
        cls.certificate_der = cls.certificate.public_bytes(serialization.Encoding.DER)
        public_numbers = cls.private_key.public_key().public_numbers()
        cls.modulus = base64.b64encode(
            public_numbers.n.to_bytes((public_numbers.n.bit_length() + 7) // 8, "big")
        )
        cls.exponent = base64.b64encode(
            public_numbers.e.to_bytes((public_numbers.e.bit_length() + 7) // 8, "big")
        )

    def _build_dte(self):
        root = etree.Element(f"{{{DTE_NS}}}DTE", nsmap={None: DTE_NS}, version="1.0")
        document = etree.SubElement(root, f"{{{DTE_NS}}}Documento", ID="F1T33")
        header = etree.SubElement(document, f"{{{DTE_NS}}}Encabezado")
        etree.SubElement(header, f"{{{DTE_NS}}}MntTotal").text = "100"
        etree.SubElement(document, f"{{{DTE_NS}}}TmstFirma").text = (
            datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        )
        return root, document

    def _digest_source(self, document, enveloped):
        if not enveloped:
            return document
        source = etree.fromstring(etree.tostring(document))
        for signature in source.xpath(".//ds:Signature", namespaces={"ds": DSIG_NS}):
            signature.getparent().remove(signature)
        return source

    def _sign(
        self,
        root,
        document,
        *,
        digest_variant="exclusivo",
        signature_variant="exclusivo",
        transforms=(DTE_C14N,),
        nested=False,
    ):
        ds = f"{{{DSIG_NS}}}"
        signature = etree.Element(ds + "Signature", nsmap={None: DSIG_NS})
        signed_info = etree.SubElement(
            signature, ds + "SignedInfo", nsmap={None: DSIG_NS}
        )
        etree.SubElement(signed_info, ds + "CanonicalizationMethod", Algorithm=DTE_C14N)
        etree.SubElement(
            signed_info, ds + "SignatureMethod", Algorithm=f"{DSIG_NS}rsa-sha1"
        )
        reference = etree.SubElement(signed_info, ds + "Reference", URI="#F1T33")
        if transforms:
            transforms_node = etree.SubElement(reference, ds + "Transforms")
            for algorithm in transforms:
                etree.SubElement(transforms_node, ds + "Transform", Algorithm=algorithm)
        etree.SubElement(reference, ds + "DigestMethod", Algorithm=f"{DSIG_NS}sha1")
        digest = hashlib.sha1(
            DIGEST_VARIANTS[digest_variant](
                self._digest_source(document, ENVELOPED_SIGNATURE in transforms)
            )
        ).digest()
        etree.SubElement(reference, ds + "DigestValue").text = base64.b64encode(
            digest
        ).decode("ascii")

        if nested:
            document.append(signature)
        else:
            document.addnext(signature)
        payload = SIGNATURE_VARIANTS[signature_variant](signed_info)
        signature_value = self.private_key.sign(
            payload, padding.PKCS1v15(), hashes.SHA1()
        )
        etree.SubElement(signature, ds + "SignatureValue").text = base64.b64encode(
            signature_value
        ).decode("ascii")
        key_info = etree.SubElement(signature, ds + "KeyInfo")
        key_value = etree.SubElement(key_info, ds + "KeyValue")
        rsa_key_value = etree.SubElement(key_value, ds + "RSAKeyValue")
        etree.SubElement(rsa_key_value, ds + "Modulus").text = self.modulus.decode()
        etree.SubElement(rsa_key_value, ds + "Exponent").text = self.exponent.decode()
        x509_data = etree.SubElement(key_info, ds + "X509Data")
        etree.SubElement(x509_data, ds + "X509Certificate").text = base64.b64encode(
            self.certificate_der
        ).decode("ascii")
        return root

    def _signed(self, **kwargs):
        root, document = self._build_dte()
        return self._sign(root, document, **kwargs)

    def _assert_rejected(self, element, message):
        with self.assertRaisesRegex(DteXmlValidationError, message):
            _verify_signature(element)

    # ------------------------------------------------------------------
    # Positivas: una por variante medida en el corpus real
    # ------------------------------------------------------------------

    def test_exclusive_digest_with_namespace_is_accepted(self):
        evidence = {}
        _verify_signature(self._signed(), evidence=evidence)
        self.assertEqual(evidence["digest_variant"], "exclusivo")
        self.assertEqual(evidence["signature_variant"], "exclusivo")
        self.assertEqual(evidence["transforms"], [DTE_C14N])

    def test_exclusive_digest_without_dte_namespace_is_accepted(self):
        evidence = {}
        _verify_signature(
            self._signed(digest_variant="exclusivo-sin-namespace"),
            evidence=evidence,
        )
        self.assertEqual(evidence["digest_variant"], "exclusivo-sin-namespace")

    def test_inclusive_signature_from_this_addon_is_accepted_as_fallback(self):
        evidence = {}
        _verify_signature(
            self._signed(digest_variant="inclusivo", signature_variant="inclusivo"),
            evidence=evidence,
        )
        self.assertEqual(evidence["digest_variant"], "inclusivo")
        self.assertEqual(evidence["signature_variant"], "inclusivo")

    def test_empty_transforms_use_implicit_c14n(self):
        evidence = {}
        _verify_signature(self._signed(transforms=()), evidence=evidence)
        self.assertEqual(evidence["transforms"], [])
        self.assertEqual(evidence["digest_variant"], "exclusivo")

    def test_enveloped_transform_removes_nested_signature_before_digest(self):
        evidence = {}
        _verify_signature(
            self._signed(transforms=(ENVELOPED_SIGNATURE, DTE_C14N), nested=True),
            evidence=evidence,
        )
        self.assertEqual(evidence["transforms"], [ENVELOPED_SIGNATURE, DTE_C14N])

    def test_enveloped_without_c14n_transform_is_accepted(self):
        evidence = {}
        _verify_signature(
            self._signed(transforms=(ENVELOPED_SIGNATURE,), nested=True),
            evidence=evidence,
        )
        self.assertEqual(evidence["transforms"], [ENVELOPED_SIGNATURE])
        self.assertEqual(evidence["digest_variant"], "exclusivo")

    # ------------------------------------------------------------------
    # Negativas obligatorias: contenido, SignedInfo y namespace
    # ------------------------------------------------------------------

    def test_one_changed_byte_in_document_fails_every_digest_variant(self):
        for digest_variant in DIGEST_VARIANTS:
            with self.subTest(digest_variant=digest_variant):
                root = self._signed(digest_variant=digest_variant)
                total = root.find(f".//{{{DTE_NS}}}MntTotal")
                total.text = "101"
                self._assert_rejected(root, "no coincide con su firma")

    def test_changed_signed_info_fails_signature_verification(self):
        root = self._signed()
        signed_info = root.find(f".//{{{DSIG_NS}}}SignedInfo")
        signed_info.set("Id", "alterado")
        self._assert_rejected(root, "firma criptográfica XMLDSIG no es válida")

    def test_signed_info_declaring_unsupported_canonicalization_is_rejected(self):
        root = self._signed()
        method = root.find(f".//{{{DSIG_NS}}}CanonicalizationMethod")
        method.set("Algorithm", "http://example.test/c14n-inventado")
        self._assert_rejected(root, "canonicalización de XMLDSIG")

    def test_changed_dte_namespace_fails_digest_verification(self):
        root = self._signed()
        raw = etree.tostring(root).replace(
            f'xmlns="{DTE_NS}"'.encode(), b'xmlns="http://www.sii.cl/SiiDteX"', 1
        )
        self._assert_rejected(etree.fromstring(raw), "no coincide con su firma")

    def test_stripped_namespace_document_is_rejected_by_the_schema(self):
        root, _document = self._build_dte()
        raw = etree.tostring(root).replace(f' xmlns="{DTE_NS}"'.encode(), b"", 1)
        stripped = etree.fromstring(raw)
        self.assertEqual(stripped.tag, "DTE")
        with self.assertRaisesRegex(DteXmlValidationError, "XSD v10"):
            validate_dte_element(stripped)

    def test_unsupported_transform_combination_is_rejected(self):
        root = self._signed(transforms=(f"{DSIG_NS}xpath", DTE_C14N))
        self._assert_rejected(root, "transformaciones de la firma")

    def test_unsigned_document_is_rejected(self):
        root, _document = self._build_dte()
        self._assert_rejected(root, "exactamente una firma")

    # ------------------------------------------------------------------
    # Corpus real: los 12 documentos aceptados por el SII
    # ------------------------------------------------------------------

    def test_real_documents_verify_with_recorded_variant(self):
        directory = os.environ.get(REAL_DTE_DIR_ENV)
        if not directory:
            self.skipTest(f"Sin {REAL_DTE_DIR_ENV} no hay corpus real que validar.")
        documents = sorted(
            path
            for folder in ("emitidos", "recibidos")
            for path in (Path(directory) / folder).glob("*.xml")
        )
        self.assertTrue(documents, "El corpus real no contiene documentos.")
        for path in documents:
            with self.subTest(document=path.name):
                evidence = {}
                validate_dte_element(
                    etree.fromstring(path.read_bytes()), evidence=evidence
                )
                self.assertIn(
                    evidence.get("digest_variant"),
                    {"exclusivo", "exclusivo-sin-namespace"},
                    f"{path.name} no verificó con una variante medida del SII",
                )
                self.assertEqual(evidence.get("signature_variant"), "exclusivo")
