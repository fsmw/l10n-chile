# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Read-only client of the SII RCV portal (``consdcvinternetui``).

The portal has no web service contract: it is the JSON API behind the RCV
pages of the SII website. The client signs in with the company certificate
(mutual TLS), takes the ``TOKEN`` cookie, replays the calls the portal makes
when it opens, and then reads the summary and the detail of one period. It
never writes anything at the SII.
"""

import base64
import binascii
import email.utils
import logging
import os
import secrets
import tempfile
import time
from contextlib import contextmanager

import requests

from odoo.exceptions import UserError
from odoo.tools.translate import LazyTranslate

from odoo.addons.l10n_cl_dte_signing.services.signature_service import (
    SignatureService,
)

from .rcv_values import is_valid_rut, normalize_vat

_logger = logging.getLogger(__name__)
_lt = LazyTranslate(__name__, default_lang="en_US")

BASE_URL = "https://www4.sii.cl"
PORTAL_URL = BASE_URL + "/consdcvinternetui/"
FACADE_URL = PORTAL_URL + "services/data/facadeService/"
PARAMETERS_URL = PORTAL_URL + "services/data/settingsService/consultarParametros"
CONFIGURATION_URL = BASE_URL + "/common-1.0/services/autConfDataService/obtieneConf"
SESSION_URL = BASE_URL + "/common-1.0/services/aaSessionService/load"
AUTH_URL = "https://herculesr.sii.cl/cgi_AUT2000/CAutInicio.cgi"
TOKEN_CHECK_URL = "https://zeusr.sii.cl/cgi_AUT2000/AutTknData.cgi"
NAMESPACE = "cl.sii.sdi.lob.diii.consdcv.data.api.interfaces.FacadeService"
# Generic client name: the portal does not require a browser identity.
# The RCV is a browser portal; keep the standard browser string the source
# module used against the real SII.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
JSON_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json;charset=UTF-8",
    "Referer": PORTAL_URL,
}
# (connect, read) seconds for every request.
TIMEOUT = (10, 30)
MAX_RETRIES = 1
RETRY_DELAY = 1
MAX_RETRY_AFTER = 30
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR = 500
SUCCESS_CODE = "0"
# A purchase detail still being prepared by the SII answers this code.
DETAIL_NOT_READY_CODE = "99"
# Odoo operation -> SII operation and the RCV states read for it.
SII_OPERATIONS = {
    "purchase": ("COMPRA", ("REGISTRO", "RECLAMADO", "PENDIENTE")),
    "sale": ("VENTA", ("REGISTRO",)),
}
DETAIL_METHODS = {
    "COMPRA": ("getDetalleCompra", "RCV_DETC"),
    "VENTA": ("getDetalleVenta", "RCV_DETV"),
}
STEP_LABELS = {
    "auth": _lt("sign-in"),
    "portal": _lt("RCV portal"),
    "configuration": _lt("session configuration"),
    "session": _lt("session load"),
    "token_check": _lt("session validation"),
    "parameters": _lt("session parameters"),
    "start": _lt("RCV start data"),
    "service": _lt("RCV query"),
}
RECEIPT_READ_UNSUPPORTED = _lt(
    "Receipt sales (boletas) cannot be read from the SII yet: there is no "
    "validated SII service for them."
)


class SiiRcvError(UserError):
    """A controlled failure of the SII read; ``code`` names the failed step."""

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


class SiiRcvClient:
    """Read the RCV sections of one period from the SII portal."""

    def __init__(self, env, session_factory=None):
        self.env = env
        self.session_factory = session_factory or requests.Session

    def fetch(self, sync):
        """Return the sections of ``sync``'s period, in the normalizer shape.

        The company RUT and certificate are checked before any request.
        """
        sync.ensure_one()
        if sync.operation not in SII_OPERATIONS:
            raise SiiRcvError(str(RECEIPT_READ_UNSUPPORTED), "receipt_unsupported")
        number, check_digit = self._company_rut(sync.company_id)
        cert_pem, key_pem = self._credentials(sync.company_id)
        with self._key_files(cert_pem, key_pem) as cert_files:
            with self.session_factory() as session:
                session.trust_env = False
                session.verify = True
                session.cert = cert_files
                session.headers.update({"User-Agent": USER_AGENT})
                token = self._bootstrap(session)
                return self._read_sections(
                    session, token, sync, {"rutEmisor": number, "dvEmisor": check_digit}
                )

    # Identity and key material

    def _company_rut(self, company):
        vat = normalize_vat(company.vat)
        if not company.vat or not is_valid_rut(vat):
            raise SiiRcvError(
                self.env._("The company has no valid RUT to read its RCV."),
                "company_rut",
            )
        return vat.split("-")

    def _credentials(self, company):
        """Return the PEM certificate and private key of the production profile."""
        profile, _holder = SignatureService().signing_identity(
            company, "production", manager_only=not self.env.su
        )
        certificate = profile.sudo().certificate_id.with_context(bin_size=False)
        try:
            cert_pem = base64.b64decode(certificate.pem_certificate, validate=True)
            key_pem = base64.b64decode(
                certificate.private_key_id.with_context(bin_size=False).pem_key,
                validate=True,
            )
        except (TypeError, ValueError, binascii.Error) as error:
            raise SiiRcvError(
                self.env._("The production certificate could not be loaded."),
                "certificate",
            ) from error
        if not cert_pem or not key_pem:
            raise SiiRcvError(
                self.env._("The production certificate has no usable private key."),
                "certificate",
            )
        return cert_pem, key_pem

    @staticmethod
    @contextmanager
    def _key_files(cert_pem, key_pem):
        """Write the TLS client material to a private directory removed on exit."""
        with tempfile.TemporaryDirectory(prefix="odoo-rcv-") as directory:
            os.chmod(directory, 0o700)
            paths = []
            for name, content in (("cert.pem", cert_pem), ("key.pem", key_pem)):
                path = os.path.join(directory, name)
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(content)
                paths.append(path)
            yield tuple(paths)

    # Session bootstrap

    def _bootstrap(self, session):
        """Sign in and open the portal session; return the session token."""
        self._step(session, "auth", "post", AUTH_URL, data={"referencia": PORTAL_URL})
        token = session.cookies.get("TOKEN")
        if not token:
            raise SiiRcvError(
                self.env._(
                    "The SII sign-in returned no session token. Check that the "
                    "production certificate is authorized for the company."
                ),
                "token",
            )
        self._step(
            session, "portal", "get", PORTAL_URL, headers={"Accept": "text/html"}
        )
        self._step(
            session,
            "configuration",
            "post",
            CONFIGURATION_URL,
            json={},
            headers=JSON_HEADERS,
        )
        self._step(session, "session", "get", SESSION_URL, headers=JSON_HEADERS)
        stamp = int(time.time() * 1000)
        self._step(
            session,
            "token_check",
            "get",
            TOKEN_CHECK_URL,
            params={
                "rnd": secrets.randbelow(10**9) / 10**9,
                "callback": f"jQuery_{stamp}",
                "_": stamp + 1,
            },
            headers={"Accept": "*/*", "Referer": PORTAL_URL},
        )
        response = self._step(
            session,
            "parameters",
            "post",
            PARAMETERS_URL,
            json=self._wrap("consultarParametros", {}, token),
            headers=JSON_HEADERS,
        )
        self._check_parameters(response)
        self._call(session, "getDatosInicio", {}, token, step="start")
        return token

    def _step(self, session, step, method, url, **kwargs):
        """Run one bootstrap request and require a 2xx answer."""
        try:
            response = getattr(session, method)(url, timeout=TIMEOUT, **kwargs)
        except requests.RequestException as error:
            raise SiiRcvError(
                self.env._(
                    "The connection with the SII failed during the %(step)s step.",
                    step=str(STEP_LABELS[step]),
                ),
                step,
            ) from error
        if not 200 <= response.status_code < 300:
            raise SiiRcvError(
                self.env._(
                    "The SII %(step)s step failed (HTTP %(status)s).",
                    step=str(STEP_LABELS[step]),
                    status=response.status_code,
                ),
                step,
            )
        return response

    def _check_parameters(self, response):
        label = str(STEP_LABELS["parameters"])
        try:
            payload = response.json()
        except ValueError as error:
            raise SiiRcvError(
                self.env._("The SII returned a non-JSON %(step)s answer.", step=label),
                "parameters",
            ) from error
        if not isinstance(payload, dict):
            raise SiiRcvError(
                self.env._(
                    "The SII returned an unexpected %(step)s answer.", step=label
                ),
                "parameters",
            )
        state = payload.get("respEstado")
        if isinstance(state, dict) and str(state.get("codRespuesta")) != SUCCESS_CODE:
            raise SiiRcvError(
                self.env._("The SII rejected the %(step)s step.", step=label),
                "parameters",
            )

    # RCV data services

    def _read_sections(self, session, token, sync, identity):
        operation, states = SII_OPERATIONS[sync.operation]
        arguments = {
            **identity,
            "ptributario": sync.period_id.tax_period,
            "operacion": operation,
        }
        return [
            self._read_section(session, token, {**arguments, "estadoContab": state})
            for state in states
        ]

    def _read_section(self, session, token, arguments):
        state = arguments["estadoContab"]
        try:
            summary = self._call(
                session,
                "getResumen",
                {**arguments, "busquedaInicial": True},
                token,
                require_code=True,
            )
        except SiiRcvError as error:
            _logger.info("RCV summary %s not read (%s).", state, error.code)
            return self._failed_section(state, "SiiSummaryError")
        rows = summary.get("data")
        if not self._is_row_list(rows):
            return self._failed_section(state, "InvalidSummary")
        details, unavailable = self._read_details(session, token, arguments, rows)
        return {
            "state": state,
            "summary": summary,
            "details": details,
            "unavailable_details": unavailable,
        }

    def _read_details(self, session, token, arguments, summary_rows):
        operation, state = arguments["operacion"], arguments["estadoContab"]
        method, recaptcha_action = DETAIL_METHODS[operation]
        allowed = (
            (DETAIL_NOT_READY_CODE,)
            if operation == "COMPRA" and state == "PENDIENTE"
            else ()
        )
        type_codes = sorted(
            {
                str(row["rsmnTipoDocInteger"])
                for row in summary_rows
                if row.get("rsmnTipoDocInteger") is not None
            }
        )
        details, unavailable = {}, []
        for type_code in type_codes:
            rows = self._read_detail(
                session,
                token,
                method,
                {
                    **arguments,
                    "codTipoDoc": type_code,
                    "accionRecaptcha": recaptcha_action,
                },
                allowed,
            )
            details[type_code] = rows
            if rows is None:
                unavailable.append(type_code)
        return details, unavailable

    def _read_detail(self, session, token, method, arguments, allowed_codes):
        """Return the detail rows of one document type, or None if unavailable."""
        try:
            detail = self._call(
                session,
                method,
                arguments,
                token,
                require_code=True,
                allowed_codes=allowed_codes,
            )
        except SiiRcvError as error:
            _logger.info(
                "RCV detail %s/%s not read (%s).",
                arguments["estadoContab"],
                arguments["codTipoDoc"],
                error.code,
            )
            return None
        rows = detail.get("data")
        code = str((detail.get("respEstado") or {}).get("codRespuesta"))
        if not self._is_row_list(rows) or code == DETAIL_NOT_READY_CODE:
            return None
        return rows

    @staticmethod
    def _failed_section(state, error):
        return {
            "state": state,
            "fetch_error": error,
            "summary": {"data": []},
            "details": {},
            "unavailable_details": [],
        }

    @staticmethod
    def _is_row_list(rows):
        return isinstance(rows, list) and all(isinstance(row, dict) for row in rows)

    @staticmethod
    def _wrap(method, data, token):
        return {
            "metaData": {
                "namespace": f"{NAMESPACE}/{method}",
                "conversationId": token,
                "transactionId": secrets.token_hex(16),
            },
            "data": data,
        }

    def _call(
        self,
        session,
        method,
        data,
        token,
        require_code=False,
        allowed_codes=(),
        step="service",
    ):
        """Call one facade method and return its JSON object answer."""
        response = self._post_with_retry(session, method, data, token, step)
        try:
            result = response.json()
        except ValueError as error:
            raise SiiRcvError(
                self.env._(
                    "The SII returned a non-JSON answer to %(method)s.", method=method
                ),
                step,
            ) from error
        if not isinstance(result, dict):
            raise SiiRcvError(
                self.env._(
                    "The SII returned an unexpected answer to %(method)s.",
                    method=method,
                ),
                step,
            )
        code = (result.get("respEstado") or {}).get("codRespuesta")
        accepted = {SUCCESS_CODE, *map(str, allowed_codes)}
        if (code is not None and str(code) not in accepted) or (
            require_code and code is None
        ):
            raise SiiRcvError(
                self.env._(
                    "The SII rejected %(method)s (functional code %(code)s).",
                    method=method,
                    code=code,
                ),
                step,
            )
        if result.get("data") is None:
            result["data"] = []
        return result

    def _post_with_retry(self, session, method, data, token, step):
        """POST to the facade, retrying once on rate limits and server errors."""
        for attempt in range(MAX_RETRIES + 1):
            last = attempt == MAX_RETRIES
            try:
                response = session.post(
                    FACADE_URL + method,
                    json=self._wrap(method, data, token),
                    headers=JSON_HEADERS,
                    timeout=TIMEOUT,
                )
            except requests.RequestException as error:
                if not last:
                    time.sleep(RETRY_DELAY)
                    continue
                raise SiiRcvError(
                    self.env._("The connection with the SII RCV service failed."), step
                ) from error
            if last:
                break
            if response.status_code == HTTP_TOO_MANY_REQUESTS:
                time.sleep(self._retry_after(response.headers.get("Retry-After")))
            elif response.status_code >= HTTP_SERVER_ERROR:
                time.sleep(RETRY_DELAY)
            else:
                break
        if response.status_code == HTTP_TOO_MANY_REQUESTS:
            raise SiiRcvError(
                self.env._("The SII is limiting queries; try again later."),
                "rate_limit",
            )
        if not 200 <= response.status_code < 300:
            raise SiiRcvError(
                self.env._(
                    "The SII RCV service answered HTTP %(status)s.",
                    status=response.status_code,
                ),
                step,
            )
        return response

    @staticmethod
    def _retry_after(value):
        """Return the Retry-After delay in seconds, between 1 and 30."""
        if not value:
            return RETRY_DELAY
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            try:
                retry_at = email.utils.parsedate_to_datetime(value)
                seconds = int(retry_at.timestamp() - time.time())
            except (TypeError, ValueError, OverflowError):
                return RETRY_DELAY
        return min(max(seconds, 1), MAX_RETRY_AFTER)
