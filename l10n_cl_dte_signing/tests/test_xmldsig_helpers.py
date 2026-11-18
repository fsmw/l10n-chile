import hashlib

from lxml import etree

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase

from odoo.addons.l10n_cl_dte_signing.services.signature_service import (
    SignatureService,
)
from odoo.addons.l10n_cl_dte_signing.services.xmldsig_helpers import (
    _canonicalize_without_sii_namespace,
)

SII_DTE = (
    b'<DTE xmlns="http://www.sii.cl/SiiDte" version="1.0">'
    b'<Documento ID="T33F7"><Encabezado><Folio>7</Folio></Encabezado>'
    b"</Documento></DTE>"
)
# Some third-party issuers digest the Documento after dropping the SII
# namespace entirely; these are the exact bytes such an issuer hashes.
NAMESPACE_FREE_C14N = (
    b'<Documento ID="T33F7"><Encabezado><Folio>7</Folio></Encabezado></Documento>'
)


def _documento(xml_bytes):
    return etree.fromstring(xml_bytes)[0]


class TestXmldsigHelpers(TransactionCase):
    def _service(self):
        # Translated validation errors resolve their language from self.env.
        service = SignatureService()
        service.env = self.env
        return service

    def test_namespace_free_c14n_drops_sii_default_namespace(self):
        canonical = _canonicalize_without_sii_namespace(_documento(SII_DTE))

        self.assertEqual(canonical, NAMESPACE_FREE_C14N)

    def test_namespace_free_c14n_ignores_comments(self):
        commented = SII_DTE.replace(
            b"<Encabezado>", b"<!-- issuer note --><Encabezado>", 1
        )

        canonical = _canonicalize_without_sii_namespace(_documento(commented))

        self.assertEqual(canonical, NAMESPACE_FREE_C14N)

    def test_namespace_free_c14n_keeps_foreign_namespaces(self):
        foreign = SII_DTE.replace(
            b"<Folio>7</Folio>", b'<Folio>7</Folio><x:Extra xmlns:x="urn:x"/>', 1
        )

        canonical = _canonicalize_without_sii_namespace(_documento(foreign))

        self.assertIn(b'<x:Extra xmlns:x="urn:x"></x:Extra>', canonical)
        self.assertNotIn(b"http://www.sii.cl/SiiDte", canonical)

    def test_namespace_free_c14n_does_not_mutate_source(self):
        document = _documento(SII_DTE)

        _canonicalize_without_sii_namespace(document)

        self.assertEqual(etree.QName(document).namespace, "http://www.sii.cl/SiiDte")

    def test_document_digest_accepts_namespace_free_variant(self):
        digest = hashlib.sha1(NAMESPACE_FREE_C14N).digest()

        variant = self._service()._verify_document_digest(_documento(SII_DTE), digest)

        self.assertEqual(variant, "exclusive_without_sii_namespace")

    def test_document_digest_rejects_tampered_namespace_free_payload(self):
        digest = hashlib.sha1(NAMESPACE_FREE_C14N).digest()
        tampered = SII_DTE.replace(b"<Folio>7</Folio>", b"<Folio>8</Folio>", 1)

        with self.assertRaises(ValidationError):
            self._service()._verify_document_digest(_documento(tampered), digest)

    def test_strict_document_digest_rejects_namespace_free_variant(self):
        digest = hashlib.sha1(NAMESPACE_FREE_C14N).digest()

        with self.assertRaises(ValidationError):
            self._service()._verify_document_digest(
                _documento(SII_DTE), digest, allow_legacy=False
            )
