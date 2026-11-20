# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""CAF acquisition boundary of a request: credentials, web flow and outcomes.

It drives :class:`SiiCafWebClient` (the SII ``of_solicita_folios`` assistant
of the request environment) with the company signing certificate, held only in
memory, and turns every outcome into a :class:`SiiCafError` code. It performs
no ORM writes and never logs the certificate, the key or the session cookies.
"""

import base64
import binascii
import logging
import unicodedata

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from lxml import etree
from lxml import html as lxml_html

from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.l10n_cl_dte_gateway.services.sii_caf_web_client import (
    PORTAL_URLS,
    SiiCafWebClient,
)
from odoo.addons.l10n_cl_dte_gateway.services.sii_caf_web_types import (
    CafAllocationRejected,
    CafDispatchUncertain,
    CafRequestRejected,
    CafSessionExpired,
    CafTransportError,
    CafTransportUnavailable,
)
from odoo.addons.l10n_cl_dte_signing.services.certificate_identity import (
    normalize_rut,
)
from odoo.addons.l10n_cl_dte_signing.services.signature_service import (
    SignatureService,
)

from .caf_errors import POST_DISPATCH, PRE_DISPATCH, SiiCafError

_logger = logging.getLogger(__name__)

CAF_DOCUMENT_CODES = (
    ("33", "33 - Electronic invoice"),
    ("34", "34 - Exempt electronic invoice"),
    ("39", "39 - Electronic receipt"),
    ("41", "41 - Exempt electronic receipt"),
    ("43", "43 - Electronic settlement invoice"),
    ("46", "46 - Electronic purchase invoice"),
    ("52", "52 - Electronic dispatch guide"),
    ("56", "56 - Electronic debit note"),
    ("61", "61 - Electronic credit note"),
    ("110", "110 - Electronic export invoice"),
    ("111", "111 - Electronic export debit note"),
    ("112", "112 - Electronic export credit note"),
)
ENVIRONMENTS = (("certification", "Certification"), ("production", "Production"))
MAX_QUANTITY = 100000
REQUEST_PATH = "/cvc_cgi/dte/of_solicita_folios"
# Normalized markers of the SII page refusing new folios while authorized
# folios remain unused.
UNUSED_FOLIO_MARKERS = (
    "folios autorizados sin usar",
    "folios sin usar",
    "folios sin utilizar",
)
REJECTION_CODES = {
    "certificate_login_rejected": "SII_AUTHENTICATION_FAILED",
    "sii_session_limit_exceeded": "SII_SESSION_LIMIT",
    "sii_unused_folios_block": "SII_FOLIO_BLOCKED",
    "sii_quantity_limit_exceeded": "SII_QUANTITY_LIMIT",
    "sii_rejected_request": "SII_HARD_REJECTION",
    "company_vat_invalid": "SII_RUT_INVALID",
}


def normalize_text(html):
    """Visible page text in lower case and without accents."""
    try:
        text = lxml_html.fromstring(html).text_content()
    except (etree.ParserError, ValueError):
        text = html
    text = " ".join(text.split()).casefold()
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


class SiiCafAcquisitionWebClient(SiiCafWebClient):
    """The assistant client that also recognizes the unused-folio block page."""

    def _inspect_page(self, html):
        text = normalize_text(html)
        if any(marker in text for marker in UNUSED_FOLIO_MARKERS):
            raise CafRequestRejected("sii_unused_folios_block")


def certificate_material(company, environment):
    """Return the PEM certificate chain and private key of the signing profile.

    The bytes live only in memory for one acquisition and are never stored.
    """
    try:
        profile, _holder = SignatureService().signing_identity(company, environment)
        certificate = profile.sudo().certificate_id
        chain = certificate._get_certificate_chain()
        if not chain or chain[0] != certificate:
            raise SiiCafError("SII_CERTIFICATE_INVALID")
        pem_chain = [
            base64.b64decode(
                item.with_context(bin_size=False).pem_certificate, validate=True
            )
            for item in chain
        ]
        key_record = certificate.private_key_id
        private_key_pem = base64.b64decode(
            key_record.with_context(bin_size=False).pem_key, validate=True
        )
        private_key = serialization.load_pem_private_key(private_key_pem, None)
        leaf = x509.load_pem_x509_certificate(pem_chain[0])
    except (AccessError, UserError, binascii.Error, TypeError, ValueError):
        raise SiiCafError("SII_CERTIFICATE_INVALID") from None
    if _public_der(private_key) != _public_der(leaf):
        raise SiiCafError("SII_CERTIFICATE_INVALID")
    return b"\n".join(pem_chain), private_key_pem


def _public_der(item):
    return item.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


class SiiCafClient:
    """Request or recover one CAF for one company in one SII environment."""

    def __init__(self, environment, transport_factory=None, credentials=None):
        if environment not in PORTAL_URLS:
            raise SiiCafError("SII_ENVIRONMENT_INVALID")
        self.environment = environment
        self.transport_factory = transport_factory
        self.credentials = credentials or certificate_material

    @property
    def endpoint(self):
        return PORTAL_URLS[self.environment] + REQUEST_PATH

    def request_caf(self, company, document_code, quantity, before_dispatch=None):
        """Allocate and download a CAF; return its ``CafResult``.

        ``before_dispatch(folio_start, folio_end)`` runs right before the POST
        that makes the SII allocate the reserved range.
        """
        document_code = self._document_code(document_code)
        quantity = self._quantity(quantity)
        vat = self._vat(company)
        with self._session(company) as (client, login):
            prepared = self._prepare(client, login, vat, document_code, quantity)
            try:
                return client.dispatch_and_download(
                    login["session"],
                    prepared,
                    vat,
                    document_code,
                    before_dispatch or (lambda _start, _end: None),
                )
            except CafAllocationRejected as error:
                # SII explicitly states that the allocation was not completed.
                raise SiiCafError(
                    "SII_QUANTITY_LIMIT",
                    POST_DISPATCH,
                    sii_code=error.code,
                    maximum_quantity=error.maximum_quantity,
                ) from None
            except CafDispatchUncertain as error:
                raise SiiCafError(
                    "SII_DISPATCH_UNCERTAIN",
                    POST_DISPATCH,
                    uncertain=True,
                    sii_code=error.code,
                    folio_start=error.folio_start,
                    folio_end=error.folio_end,
                ) from None

    def recover_caf(self, company, document_code, folio_start, folio_end):
        """Download an already authorized range; this never allocates folios."""
        document_code = self._document_code(document_code)
        vat = self._vat(company)
        with self._session(company) as (client, login):
            try:
                return client.recover_caf(
                    login["session"], vat, document_code, folio_start, folio_end
                )
            except CafSessionExpired:
                login["session"] = self._login(client, login)
                return client.recover_caf(
                    login["session"], vat, document_code, folio_start, folio_end
                )

    def _session(self, company):
        return _AssistantSession(self, company)

    def _login(self, client, login):
        return client.authenticate(login["certificate"], login["key"])

    def _prepare(self, client, login, vat, document_code, quantity):
        try:
            return client.prepare_request(
                login["session"], vat, document_code, quantity
            )
        except CafSessionExpired:
            login["session"] = self._login(client, login)
            return client.prepare_request(
                login["session"], vat, document_code, quantity
            )

    @staticmethod
    def _document_code(document_code):
        code = str(document_code or "").strip()
        if code not in dict(CAF_DOCUMENT_CODES):
            raise SiiCafError("SII_DOCUMENT_TYPE_INVALID")
        return code

    @staticmethod
    def _quantity(quantity):
        if not isinstance(quantity, int) or not 0 < quantity <= MAX_QUANTITY:
            raise SiiCafError("SII_QUANTITY_INVALID")
        return quantity

    @staticmethod
    def _vat(company):
        try:
            return normalize_rut(company.sudo().vat)
        except (ValidationError, ValueError, TypeError):
            raise SiiCafError("SII_RUT_INVALID") from None


class _AssistantSession:
    """Log in on enter; always log out and map pre-dispatch failures on exit."""

    def __init__(self, owner, company):
        self.owner = owner
        self.company = company
        self.client = None

    def __enter__(self):
        certificate, key = self.owner.credentials(self.company, self.owner.environment)
        transport = (
            self.owner.transport_factory() if self.owner.transport_factory else None
        )
        try:
            self.client = SiiCafAcquisitionWebClient(
                transport=transport, environment=self.owner.environment
            )
            login = {"certificate": certificate, "key": key}
            login["session"] = self.owner._login(self.client, login)
        except Exception as error:
            self.__exit__(type(error), error, None)
            raise
        return self.client, login

    def __exit__(self, exc_type, error, traceback):
        if self.client is not None:
            client, self.client = self.client, None
            client.close()
        if error is None or isinstance(error, SiiCafError):
            return False
        raise _classify(error) from None


def _classify(error):
    """Map a web assistant failure that happened before any allocation POST."""
    if isinstance(error, CafTransportUnavailable):
        return SiiCafError("SII_TRANSPORT_UNAVAILABLE", sii_code=str(error))
    if isinstance(error, CafSessionExpired):
        return SiiCafError("SII_SESSION_EXPIRED")
    if isinstance(error, CafTransportError):
        return SiiCafError("SII_TRANSPORT_ERROR")
    if isinstance(error, CafAllocationRejected):
        return SiiCafError(
            "SII_QUANTITY_LIMIT",
            sii_code=error.code,
            maximum_quantity=error.maximum_quantity,
        )
    if isinstance(error, CafRequestRejected):
        if error.code.startswith("caf_recovery"):
            return SiiCafError("SII_RECOVERY_FAILED", sii_code=error.code)
        return SiiCafError(
            REJECTION_CODES.get(error.code, "SII_ASSISTANT_ERROR"),
            sii_code=error.code,
        )
    _logger.warning("SII CAF assistant stopped (%s)", type(error).__name__)
    return SiiCafError("UNEXPECTED_ERROR", PRE_DISPATCH)
