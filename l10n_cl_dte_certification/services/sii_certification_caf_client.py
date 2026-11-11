# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""HTTP client for the legacy SII Certification CAF assistant."""

import email.utils
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit
from zoneinfo import ZoneInfo

from lxml import etree
from lxml import html as lxml_html

from odoo.addons.l10n_cl_dte_core.tools.xml_safety import safe_fromstring

AUTH_URL = "https://herculesr.sii.cl"
SESSION_URL = "https://zeusr.sii.cl"
LOGOUT_URL = SESSION_URL + "/cgi_AUT2000/autTermino.cgi"
PORTAL_URL = "https://maullin.sii.cl"
IDK = "100"
MAX_REDIRECTS = 10
MAX_RESPONSE_BYTES = 1024 * 1024
CONNECT_TIMEOUT = 15
LOGIN_TIMEOUT = 45
LOGOUT_TIMEOUT = 15
ASSISTANT_TIMEOUT = 70
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/152.0.0.0 Safari/537.36"
)
LOGIN_MARKERS = (
    "autInicioDTE",
    "IngresoCertificado",
    "InicioAutenticacion",
    "CAutInicio.cgi",
    "CAutValida",
)
ERROR_MARKERS = (
    "El código de este mensaje es",
    "no se encuentra autorizado",
    "no está autorizado",
    "Usted no tiene",
    "no es representante",
    "no puede solicitar",
)

_logger = logging.getLogger(__name__)


class CafTransportUnavailable(Exception):
    """The local PycURL/libcurl runtime cannot meet the in-memory mTLS contract."""


class CafSessionExpired(Exception):
    """The AUT2000 session expired before a folio-generation POST."""


class CafRequestRejected(Exception):
    """The SII rejected a request before the irreversible POST."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


class CafAllocationRejected(CafRequestRejected):
    """SII explicitly confirms that this request did not allocate folios."""

    def __init__(self, code, maximum_quantity=None):
        self.maximum_quantity = maximum_quantity
        super().__init__(code)


class CafDispatchUncertain(Exception):
    """A folio-generation POST may have reached SII, so it must not be retried."""

    def __init__(self, folio_start, folio_end, code):
        self.folio_start = folio_start
        self.folio_end = folio_end
        self.code = code
        super().__init__(code)


class CafTransportError(Exception):
    """A network transfer failed without exposing raw transport details."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict
    body: bytes


@dataclass(frozen=True)
class CafResult:
    content: bytes
    document_code: str
    issuer_vat: str
    idk: str
    folio_start: int
    folio_end: int
    date_from: date


class PycurlTransport:
    """Reusable easy handle using libcurl memory-blob options for client mTLS."""

    def __init__(self):
        try:
            import pycurl
        except ImportError as error:
            raise CafTransportUnavailable("pycurl_missing") from error
        self.pycurl = pycurl
        self._check_runtime()
        self.handle = pycurl.Curl()

    def _check_runtime(self):
        pycurl = self.pycurl
        required_options = ("SSLCERT_BLOB", "SSLKEY_BLOB")
        if any(not hasattr(pycurl, option) for option in required_options):
            raise CafTransportUnavailable("pycurl_blob_options_missing")
        try:
            version_info = pycurl.version_info()
            # version_info()[1] is text; [2] is libcurl's numeric version.
            libcurl_version = version_info[2]
            if not isinstance(libcurl_version, int):
                raise TypeError("Invalid numeric libcurl version")
            ssl_version = str(version_info[5] or "")
        except (AttributeError, IndexError, TypeError) as error:
            raise CafTransportUnavailable("libcurl_runtime_unavailable") from error
        if libcurl_version < 0x074700:
            raise CafTransportUnavailable("libcurl_version_unsupported")
        if "openssl" not in ssl_version.lower():
            raise CafTransportUnavailable("libcurl_openssl_backend_required")

    def request(self, method, url, headers, body=None, certificate=None, key=None):
        pycurl = self.pycurl
        response_headers = {}
        response_body = bytearray()
        too_large = False
        handle = self.handle
        handle.reset()

        def receive_header(raw_line):
            line = raw_line.decode("latin-1", errors="replace").strip()
            if line.upper().startswith("HTTP/"):
                response_headers.clear()
            elif ":" in line:
                name, value = line.split(":", 1)
                response_headers.setdefault(name.lower().strip(), []).append(
                    value.strip()
                )
            return len(raw_line)

        def receive_body(chunk):
            nonlocal too_large
            if len(response_body) + len(chunk) > MAX_RESPONSE_BYTES:
                too_large = True
                return 0
            response_body.extend(chunk)
            return len(chunk)

        options = {
            pycurl.URL: url,
            pycurl.CUSTOMREQUEST: method,
            pycurl.FOLLOWLOCATION: False,
            pycurl.CONNECTTIMEOUT: CONNECT_TIMEOUT,
            pycurl.TIMEOUT: (
                LOGOUT_TIMEOUT
                if url == LOGOUT_URL
                else LOGIN_TIMEOUT
                if certificate
                else ASSISTANT_TIMEOUT
            ),
            pycurl.SSL_VERIFYPEER: 1,
            pycurl.SSL_VERIFYHOST: 2,
            pycurl.HTTPHEADER: [f"{name}: {value}" for name, value in headers.items()],
            pycurl.HEADERFUNCTION: receive_header,
            pycurl.WRITEFUNCTION: receive_body,
            pycurl.ACCEPT_ENCODING: "",
        }
        protocol_option = getattr(pycurl, "PROTOCOLS", None)
        https_protocol = getattr(pycurl, "PROTO_HTTPS", None)
        if https_protocol is None:
            https_protocol = getattr(pycurl, "CURLPROTO_HTTPS", None)
        if protocol_option is not None and https_protocol is not None:
            options[protocol_option] = https_protocol
        if body is not None:
            options[pycurl.POSTFIELDS] = body
        if certificate is not None:
            options[pycurl.SSLCERT_BLOB] = certificate
            options[pycurl.SSLCERTTYPE] = "PEM"
            options[pycurl.SSLKEY_BLOB] = key
            options[pycurl.SSLKEYTYPE] = "PEM"

        try:
            for option, value in options.items():
                handle.setopt(option, value)
            handle.perform()
            status = handle.getinfo(pycurl.RESPONSE_CODE)
        except Exception as error:
            if too_large:
                raise CafTransportError("response_too_large") from error
            # Do not put libcurl error strings in user-visible diagnostics.
            raise CafTransportError("http_transfer_failed") from error
        finally:
            handle.reset()
        return HttpResponse(int(status), response_headers, bytes(response_body))

    def close(self):
        self.handle.close()


class SiiCafSession:
    """AUT2000 cookies held only in memory for one requested batch."""

    def __init__(self):
        self.cookies = {}

    def cookie_header(self, url):
        host = _host(url)
        values = []
        for cookie in self.cookies.values():
            domain = cookie["domain"]
            matches = host == domain or (
                not cookie["host_only"] and host.endswith("." + domain)
            )
            if matches:
                values.append(f"{cookie['name']}={cookie['value']}")
        return "; ".join(values) or None

    def receive(self, url, response):
        host = _host(url)
        for value in response.headers.get("set-cookie", []):
            self._apply_cookie(value, host)

    def receive_javascript(self, html):
        for value in re.findall(
            r"document\.cookie\s*=\s*[\"']([^\"']+)[\"']", html, re.I
        ):
            self._apply_cookie(value, "sii.cl", default_domain=True)

    def has_token(self):
        return any(
            cookie["name"] == "TOKEN" and cookie["value"]
            for cookie in self.cookies.values()
        )

    def _apply_cookie(self, value, host, default_domain=False):
        parts = [part.strip() for part in value.split(";")]
        pair = parts.pop(0)
        if "=" not in pair:
            return
        name, cookie_value = (part.strip() for part in pair.split("=", 1))
        if not name:
            return
        domain = None
        expired = not cookie_value
        for attribute in parts:
            key, separator, attr_value = attribute.partition("=")
            key = key.lower().strip()
            attr_value = attr_value.strip()
            if key == "domain" and separator:
                domain = attr_value.lower().lstrip(".")
            elif key == "max-age" and attr_value.lstrip("-").isdigit():
                expired = expired or int(attr_value) <= 0
            elif key == "expires" and separator:
                try:
                    expired = (
                        expired
                        or email.utils.parsedate_to_datetime(attr_value).timestamp()
                        <= time.time()
                    )
                except (TypeError, ValueError, OverflowError):
                    continue
        if domain is not None:
            if not (host == domain or host.endswith("." + domain)):
                return
            if domain != "sii.cl" and not domain.endswith(".sii.cl"):
                return
        host_only = domain is None and not default_domain
        domain = domain or host
        key = (name, domain, host_only)
        if expired:
            self.cookies.pop(key, None)
        else:
            self.cookies[key] = {
                "name": name,
                "value": cookie_value,
                "domain": domain,
                "host_only": host_only,
            }


class _FormParser(HTMLParser):
    """Extract the first form's action and controls without executing markup."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.action = None
        self.inputs = {}
        self._inside = False
        self._select = None
        self.text_parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag.lower() == "form" and self.action is None:
            self._inside = True
            self.action = attrs.get("action", "")
        elif self._inside and tag.lower() == "input":
            name = attrs.get("name")
            if name and attrs.get("type", "").lower() not in {"button", "reset"}:
                self.inputs[name] = attrs.get("value", "")
        elif self._inside and tag.lower() == "select":
            name = attrs.get("name")
            if name:
                self._select = {"name": name, "value": "", "selected": False}
        elif self._inside and tag.lower() == "option" and self._select:
            value = attrs.get("value", "")
            if not self._select["selected"] and not self._select["value"]:
                self._select["value"] = value
            if "selected" in attrs:
                self._select["value"] = value
                self._select["selected"] = True

    def handle_endtag(self, tag):
        if tag.lower() == "form" and self._inside:
            self._inside = False
        elif tag.lower() == "select" and self._select:
            self.inputs[self._select["name"]] = self._select["value"]
            self._select = None

    def handle_data(self, data):
        self.text_parts.append(data)


class SiiCertificationCafClient:
    """Implement the five-step SII assistant flow for Certification only."""

    def __init__(self, transport=None):
        self.transport = transport or PycurlTransport()
        self._session = None
        self._closed = False

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.logout()
        except Exception as error:  # pylint: disable=broad-except
            # Cleanup must not replace a persisted fiscal result with an error.
            _logger.warning(
                "SII session logout could not be confirmed (%s)",
                type(error).__name__,
            )
        finally:
            close = getattr(self.transport, "close", None)
            if close:
                close()

    def logout(self):
        """End only this client's AUT2000 session before discarding its cookies."""
        session, self._session = self._session, None
        if session is None:
            return
        try:
            if not session.has_token():
                return
            # This is the official site's Cerrar Sesión link. Never follow a
            # redirect or send certificate material as part of session cleanup.
            response = self.transport.request(
                "GET",
                LOGOUT_URL,
                {
                    "User-Agent": USER_AGENT,
                    "Referer": PORTAL_URL + "/",
                    "Cookie": session.cookie_header(LOGOUT_URL) or "",
                },
            )
            if not 200 <= response.status < 400:
                raise CafTransportError("session_logout_failed")
        finally:
            session.cookies.clear()

    def authenticate(self, certificate_pem, private_key_pem):
        if self._closed:
            raise CafRequestRejected("client_closed")
        self.logout()
        session = SiiCafSession()
        self._session = session
        reference = PORTAL_URL + "/cvc_cgi/dte/of_solicita_folios"
        url = AUTH_URL + "/cgi_AUT2000/CAutInicio.cgi?" + reference
        response = self._request_redirects(
            session,
            "POST",
            url,
            {"referencia": reference},
            {
                "Origin": SESSION_URL,
                "Referer": SESSION_URL
                + "/AUT2000/InicioAutenticacion/IngresoCertificado.html?"
                + reference,
            },
            lambda target: _validate_sii_host(target, allow_subdomains=True),
            certificate_pem,
            private_key_pem,
            LOGIN_TIMEOUT,
        )
        html = _decode_html(response.body)
        session.receive_javascript(html)
        if not session.has_token():
            normalized = "".join(
                char
                for char in unicodedata.normalize("NFKD", html.lower())
                if not unicodedata.combining(char)
            )
            if "maximo de sesiones autenticadas" in normalized or re.search(
                r"\b\d{2}(?:\.\d+){4,}\.27\b", normalized
            ):
                raise CafRequestRejected("sii_session_limit_exceeded")
            raise CafRequestRejected("certificate_login_rejected")
        return session

    def prepare_request(
        self,
        session,
        company_vat,
        document_code,
        quantity,
        *,
        check_quantity_limit=True,
    ):
        """Navigate to the SII confirmation page and return its reserved range."""
        if quantity < 1:
            raise CafRequestRejected("quantity_must_be_positive")
        rut, dv = _split_vat(company_vat)
        base = PORTAL_URL + "/cvc_cgi/dte"
        try:
            html = self._assistant_request(
                session,
                "GET",
                base + "/of_solicita_folios",
                None,
                base + "/of_solicita_folios",
            )
            _check_session(html)
            overrides = {
                "RUT_EMP": rut,
                "DV_EMP": dv,
                "FOLIO_INICIAL": "0",
                "COD_DOCTO": str(document_code),
                "CANT_DOCTOS": str(quantity),
                "ACEPTAR": "Continuar",
            }
            referer = base + "/of_solicita_folios"
            for _attempt in range(4):
                form = _parse_form(html)
                if check_quantity_limit:
                    _check_quantity_limit(form["inputs"], quantity)
                action = _resolve_url(base + "/", form["action"])
                _validate_assistant_url(action)
                html = self._assistant_request(
                    session,
                    "POST",
                    action,
                    {**form["inputs"], **overrides},
                    referer,
                )
                _check_session(html)
                _check_sii_error(html)
                referer = action
                if "of_genera_folio" in _parse_form(html)["action"]:
                    break
            confirmation = _parse_form(html)
            if check_quantity_limit:
                _check_quantity_limit(confirmation["inputs"], quantity)
            if "of_genera_folio" not in confirmation["action"]:
                raise CafRequestRejected("folio_confirmation_missing")
            action = _resolve_url(base + "/", confirmation["action"])
            _validate_assistant_url(action)
            try:
                folio_start = int(confirmation["inputs"].get("FOLIO_INI", ""))
                folio_end = int(confirmation["inputs"].get("FOLIO_FIN", ""))
            except ValueError as error:
                raise CafRequestRejected("folio_range_missing") from error
            if (
                folio_start < 1
                or folio_end < folio_start
                or folio_end - folio_start + 1 != quantity
                or not confirmation["inputs"].get("NOMUSU")
            ):
                raise CafRequestRejected("folio_range_unexpected")
            return action, confirmation, referer, rut, dv, folio_start, folio_end
        except CafSessionExpired:
            raise
        except CafRequestRejected:
            raise
        except Exception as error:
            raise CafRequestRejected("sii_pre_dispatch_failed") from error

    def dispatch_and_download(
        self,
        session,
        prepared,
        company_vat,
        document_code,
        before_dispatch,
    ):
        """Persist the reserved range, make the irreversible POST, then fetch CAF."""
        action, confirmation, referer, rut, dv, folio_start, folio_end = prepared
        before_dispatch(folio_start, folio_end)
        base = PORTAL_URL + "/cvc_cgi/dte"
        try:
            page = self._assistant_request(
                session,
                "POST",
                action,
                {**confirmation["inputs"], "ACEPTAR": "Obtener Folios"},
                referer,
            )
            _check_sii_error(page)
            download_form = _parse_form(page)
            download_url = _resolve_url(base + "/", download_form["action"])
            _validate_assistant_url(download_url)
            download_inputs = {
                **download_form["inputs"],
                "RUT_EMP": rut,
                "DV_EMP": dv,
                "COD_DOCTO": str(document_code),
                "FOLIO_INI": str(folio_start),
                "FOLIO_FIN": str(folio_end),
                "FECHA": datetime.now(ZoneInfo("America/Santiago")).date().isoformat(),
                "ACEPTAR": "AQUI",
            }
            response = self._request_redirects(
                session,
                "POST",
                download_url,
                download_inputs,
                {"Referer": base + "/of_genera_folio"},
                _validate_assistant_url,
                timeout=ASSISTANT_TIMEOUT,
                preserve_body_on_redirect=True,
            )
            result = _parse_caf(response.body)
            normalized_company = _normalize_vat(company_vat)
            if (
                result.idk != IDK
                or _normalize_vat(result.issuer_vat) != normalized_company
                or result.document_code != str(document_code)
                or result.folio_start != folio_start
                or result.folio_end != folio_end
            ):
                raise CafDispatchUncertain(
                    folio_start, folio_end, "caf_identity_or_range_mismatch"
                )
            return result
        except (CafDispatchUncertain, CafAllocationRejected):
            raise
        except Exception as error:
            raise CafDispatchUncertain(
                folio_start, folio_end, "caf_download_or_validation_uncertain"
            ) from error

    def request_caf(
        self,
        session,
        company_vat,
        document_code,
        quantity,
        before_dispatch,
    ):
        prepared = self.prepare_request(session, company_vat, document_code, quantity)
        return self.dispatch_and_download(
            session,
            prepared,
            company_vat,
            document_code,
            before_dispatch,
        )

    def recover_caf(self, session, company_vat, document_code, folio_start, folio_end):
        """Download one already authorized range; never enter folio generation."""
        if folio_start < 1 or folio_end < folio_start:
            raise CafRequestRejected("caf_recovery_range_invalid")
        rut, dv = _split_vat(company_vat)
        entry_url = PORTAL_URL + "/cvc_cgi/dte/rf_reobtencion1_folios"
        response = self._request_redirects(
            session, "GET", entry_url, None, {}, _validate_recovery_url
        )
        page = _decode_html(response.body)
        _check_recovery_session(page)
        form = _parse_form(page)
        action = _resolve_url(entry_url, form["action"])
        _validate_recovery_url(action)
        if urlsplit(action).path != "/cvc_cgi/dte/rf_reobtencion2_folios":
            raise CafRequestRejected("caf_recovery_form_invalid")
        response = self._request_redirects(
            session,
            "POST",
            action,
            {
                **form["inputs"],
                "RUT_EMP": rut,
                "DV_EMP": dv,
                "COD_DOCTO": str(document_code),
            },
            {"Referer": entry_url},
            _validate_recovery_url,
        )
        page = _decode_html(response.body)
        _check_recovery_session(page)
        _check_sii_error(page)
        tree = lxml_html.fromstring(page)
        matches = []
        for candidate in tree.xpath("//form[@action]"):
            inputs = {
                node.get("name"): node.get("value", "")
                for node in candidate.xpath(".//input[@name]")
            }
            if (
                inputs.get("FOLIO_INI") != str(folio_start)
                or inputs.get("FOLIO_FIN") != str(folio_end)
                or inputs.get("COD_DOCTO") != str(document_code)
                or inputs.get("RUT_EMP") != rut
                or inputs.get("DV_EMP", "").upper() != dv
            ):
                continue
            download_url = _resolve_url(action, candidate.get("action"))
            _validate_recovery_url(download_url)
            if urlsplit(download_url).path != "/cvc_cgi/dte/rf_reobtencion3_folios":
                raise CafRequestRejected("caf_recovery_form_invalid")
            matches.append((download_url, inputs))
        if not matches:
            raise CafRequestRejected("caf_recovery_not_found")
        if len(matches) != 1:
            raise CafRequestRejected("caf_recovery_ambiguous")
        download_url, inputs = matches[0]
        response = self._request_redirects(
            session,
            "POST",
            download_url,
            inputs,
            {"Referer": action},
            _validate_recovery_url,
            preserve_body_on_redirect=True,
        )
        # The rf_ endpoints re-download the already authorized range.
        # The of_ allocation assistant is never allowed by this validator.
        for endpoint in ("rf_genera_folio", "rf_genera_archivo"):
            if b"<AUTORIZACION" in response.body[:200]:
                break
            page = _decode_html(response.body)
            _check_recovery_session(page)
            _check_sii_error(page)
            form = _parse_form(page)
            next_url = _resolve_url(download_url, form["action"])
            _validate_recovery_url(next_url)
            inputs = form["inputs"]
            if (
                urlsplit(next_url).path != "/cvc_cgi/dte/" + endpoint
                or inputs.get("FOLIO_INI") != str(folio_start)
                or inputs.get("FOLIO_FIN") != str(folio_end)
                or inputs.get("COD_DOCTO") != str(document_code)
                or inputs.get("RUT_EMP") != rut
                or inputs.get("DV_EMP", "").upper() != dv
            ):
                raise CafRequestRejected("caf_recovery_form_invalid")
            response = self._request_redirects(
                session,
                "POST",
                next_url,
                inputs,
                {"Referer": download_url},
                _validate_recovery_url,
                preserve_body_on_redirect=True,
            )
            download_url = next_url
        result = _parse_caf(response.body)
        if (
            result.idk != IDK
            or _normalize_vat(result.issuer_vat) != _normalize_vat(company_vat)
            or result.document_code != str(document_code)
            or result.folio_start != folio_start
            or result.folio_end != folio_end
        ):
            raise CafRequestRejected("caf_recovery_identity_mismatch")
        return result

    def _assistant_request(self, session, method, url, values, referer):
        response = self._request_redirects(
            session,
            method,
            url,
            values,
            {"Referer": referer},
            _validate_assistant_url,
            timeout=ASSISTANT_TIMEOUT,
        )
        html = _decode_html(response.body)
        _check_session(html)
        return html

    def _request_redirects(
        self,
        session,
        method,
        url,
        values,
        headers,
        validate_url,
        certificate=None,
        private_key=None,
        timeout=ASSISTANT_TIMEOUT,
        preserve_body_on_redirect=False,
    ):
        for redirect_count in range(MAX_REDIRECTS + 1):
            validate_url(url)
            request_headers = {"User-Agent": USER_AGENT, **headers}
            cookie = session.cookie_header(url)
            if cookie:
                request_headers["Cookie"] = cookie
            body = urlencode(values, doseq=True).encode("utf-8") if values else None
            if body is not None:
                request_headers["Content-Type"] = "application/x-www-form-urlencoded"
            try:
                response = self.transport.request(
                    method,
                    url,
                    request_headers,
                    body=body,
                    certificate=certificate,
                    key=private_key,
                )
            except Exception as error:
                raise CafTransportError("http_transfer_failed") from error
            session.receive(url, response)
            locations = response.headers.get("location", [])
            if response.status not in {301, 302, 303, 307, 308}:
                if not 200 <= response.status < 300:
                    raise CafTransportError("http_status_error")
                return response
            if redirect_count >= MAX_REDIRECTS or not locations:
                raise CafTransportError("redirect_limit_or_location_missing")
            destination = _resolve_url(url, locations[-1])
            validate_url(destination)
            headers = {**headers, "Referer": url}
            if response.status not in {307, 308} and not preserve_body_on_redirect:
                method, values = "GET", None
            url = destination
        raise CafTransportError("redirect_limit_exceeded")


def _validate_sii_host(url, allow_subdomains=False):
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if (
        parts.scheme != "https"
        or parts.username
        or parts.password
        or parts.port not in (None, 443)
        or not (host == "sii.cl" or (allow_subdomains and host.endswith(".sii.cl")))
    ):
        raise CafRequestRejected("redirect_outside_sii")


def _validate_assistant_url(url):
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or (parts.hostname or "").lower() != "maullin.sii.cl"
        or parts.username
        or parts.password
        or parts.port not in (None, 443)
    ):
        raise CafRequestRejected("assistant_url_outside_certification")


def _validate_recovery_url(url):
    _validate_assistant_url(url)
    if urlsplit(url).path not in {
        "/cvc_cgi/dte/rf_reobtencion1_folios",
        "/cvc_cgi/dte/rf_reobtencion2_folios",
        "/cvc_cgi/dte/rf_reobtencion3_folios",
        "/cvc_cgi/dte/rf_genera_folio",
        "/cvc_cgi/dte/rf_genera_archivo",
    }:
        raise CafRequestRejected("caf_recovery_url_forbidden")


def _check_recovery_session(page):
    _check_session(page)
    if "no se encuentra autenticado" in page.lower():
        raise CafSessionExpired("sii_session_expired")


def _host(url):
    return (urlsplit(url).hostname or "").lower()


def _resolve_url(base, reference):
    return urljoin(base, (reference or "").strip())


def _parse_form(html):
    parser = _FormParser()
    try:
        parser.feed(html)
        parser.close()
    except (AssertionError, ValueError) as error:
        raise CafRequestRejected("sii_form_invalid") from error
    if parser.action is None:
        _check_sii_error(html)
        raise CafRequestRejected("sii_form_missing")
    return {"action": parser.action, "inputs": parser.inputs}


def _decode_html(content):
    if content.startswith(b"\xef\xbb\xbf"):
        content = content[3:]
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("iso-8859-1")


def _check_session(text):
    if any(marker in text for marker in LOGIN_MARKERS):
        raise CafSessionExpired("sii_session_expired")


def _check_sii_error(html):
    # Only an explicit non-completion response is a definite allocation failure.
    text = " ".join(lxml_html.fromstring(html).text_content().split()).lower()
    normalized = "".join(
        char
        for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )
    if (
        "no ha sido posible completar su solicitud" in normalized
        and "cantidad de documentos a timbrar debe ser menor o igual" in normalized
        and "maximo autorizado" in normalized
    ):
        raise CafAllocationRejected("sii_quantity_limit_exceeded")
    lower = html.lower()
    if any(marker.lower() in lower for marker in ERROR_MARKERS):
        raise CafRequestRejected("sii_rejected_request")


def _check_quantity_limit(inputs, quantity):
    maximum = (inputs.get("MAX_AUTOR") or "").strip()
    if maximum.isdigit() and quantity > int(maximum):
        raise CafAllocationRejected("sii_quantity_limit_exceeded", int(maximum))


def _split_vat(value):
    normalized = re.sub(r"[^0-9Kk-]", "", value or "").upper()
    if "-" not in normalized:
        raise CafRequestRejected("company_vat_invalid")
    rut, dv = normalized.rsplit("-", 1)
    if not rut.isdigit() or len(dv) != 1 or not (dv.isdigit() or dv == "K"):
        raise CafRequestRejected("company_vat_invalid")
    return rut, dv


def _normalize_vat(value):
    return re.sub(r"[^0-9Kk]", "", value or "").upper()


def _parse_caf(content):
    if not content or len(content) > MAX_RESPONSE_BYTES:
        raise CafRequestRejected("caf_size_invalid")
    upper = content.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise CafRequestRejected("caf_dtd_forbidden")
    try:
        root = safe_fromstring(content)
    except (etree.XMLSyntaxError, ValueError) as error:
        raise CafRequestRejected("caf_xml_invalid") from error
    if etree.QName(root).localname != "AUTORIZACION":
        raise CafRequestRejected("caf_root_invalid")
    data_nodes = root.xpath("./*[local-name()='CAF']/*[local-name()='DA']")
    if len(data_nodes) != 1:
        raise CafRequestRejected("caf_metadata_missing")
    data = data_nodes[0]

    def text(path):
        nodes = data.xpath(path)
        return (nodes[0].text or "").strip() if len(nodes) == 1 else ""

    try:
        folio_start = int(text("./*[local-name()='RNG']/*[local-name()='D']"))
        folio_end = int(text("./*[local-name()='RNG']/*[local-name()='H']"))
        date_from = date.fromisoformat(text("./*[local-name()='FA']"))
    except ValueError as error:
        raise CafRequestRejected("caf_metadata_invalid") from error
    values = {
        "document_code": text("./*[local-name()='TD']"),
        "issuer_vat": text("./*[local-name()='RE']"),
        "idk": text("./*[local-name()='IDK']"),
    }
    if not all(values.values()) or folio_start < 1 or folio_end < folio_start:
        raise CafRequestRejected("caf_metadata_invalid")
    return CafResult(
        content=content,
        folio_start=folio_start,
        folio_end=folio_end,
        date_from=date_from,
        **values,
    )
