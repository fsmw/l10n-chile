# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Errors, results and the form parser of the SII Certification CAF assistant."""

from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser


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
