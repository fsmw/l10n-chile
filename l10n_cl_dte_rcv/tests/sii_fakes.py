# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Recorded, synthetic SII portal answers for the RCV client tests.

Every RUT, folio and amount is invented. No request leaves the process.
"""

import os
import stat

from requests.cookies import RequestsCookieJar

from ..services import sii_rcv_client as client

SYNTHETIC_TOKEN = "SYNTHETICTOKEN0001"
NO_JSON = object()


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._payload = {} if payload is None else payload

    def json(self):
        if self._payload is NO_JSON:
            raise ValueError("not JSON")
        return self._payload


def ok(data=None, code=0):
    return FakeResponse(payload={"respEstado": {"codRespuesta": code}, "data": data})


def summary_row(count, net=100, vat=19, type_code=33):
    return {
        "rsmnTipoDocInteger": type_code,
        "rsmnTotDoc": count,
        "rsmnMntNeto": net * count,
        "rsmnMntExe": 0,
        "rsmnMntIVA": vat * count,
        "rsmnMntTotal": (net + vat) * count,
    }


def detail_row(folio, rut=76222222, check_digit="1", net=100, vat=19, type_code=33):
    return {
        "detTipoDoc": type_code,
        "detNroDoc": folio,
        "detFchDoc": "15/08/2026",
        "detRutDoc": rut,
        "detDvDoc": check_digit,
        "detRznSoc": "Synthetic Counterpart",
        "detMntNeto": net,
        "detMntExe": 0,
        "detMntIVA": vat,
        "detMntTotal": net + vat,
    }


class FakeSiiSession:
    """Stand-in for ``requests.Session`` that answers like the RCV portal.

    ``summaries`` maps an RCV state to its summary rows; ``details`` maps
    ``(state, type code)`` to detail rows or to a ``FakeResponse``.
    ``overrides`` replaces the answer of one URL (a response, an exception,
    a callable taking the request keyword arguments, or a list of those
    answered in turn).
    """

    def __init__(self, summaries=None, details=None, overrides=None):
        self.summaries = summaries or {}
        self.details = details or {}
        self.overrides = overrides or {}
        self.calls = []
        self.cookies = RequestsCookieJar()
        self.headers = {}
        self.cert = None
        self.verify = None
        self.trust_env = True
        self.key_file_modes = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def get(self, url, **kwargs):
        return self._answer("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self._answer("POST", url, kwargs)

    def facade_calls(self, method):
        url = client.FACADE_URL + method
        return [
            kwargs["json"]["data"]
            for _verb, called, kwargs in self.calls
            if called == url
        ]

    def _answer(self, verb, url, kwargs):
        self.calls.append((verb, url, kwargs))
        override = self.overrides.get(url)
        if isinstance(override, list):
            override = override.pop(0)
        if isinstance(override, Exception):
            raise override
        if callable(override):
            return override(kwargs)
        if override is not None:
            return override
        if url == client.AUTH_URL:
            return self._sign_in()
        if url == client.PARAMETERS_URL:
            return ok({})
        if url.startswith(client.FACADE_URL):
            return self._facade(url[len(client.FACADE_URL) :], kwargs["json"]["data"])
        return FakeResponse(payload={})

    def _sign_in(self):
        self.key_file_modes = [
            stat.S_IMODE(os.stat(path).st_mode) for path in self.cert or ()
        ]
        self.cookies.set("TOKEN", SYNTHETIC_TOKEN)
        return FakeResponse(payload=NO_JSON)

    def _facade(self, method, data):
        if method == "getResumen":
            return ok(self.summaries.get(data["estadoContab"], []))
        if method in ("getDetalleCompra", "getDetalleVenta"):
            detail = self.details.get((data["estadoContab"], data["codTipoDoc"]), [])
            return detail if isinstance(detail, FakeResponse) else ok(detail)
        return ok({})
