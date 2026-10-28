# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Client for the Banco Central de Chile BDE REST API."""

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime


class BcentralApi:
    """Fetch official BDE observations without exposing the API token."""

    BASE_URL = "https://si3.bcentral.cl/SieteRestWS/SieteRestWS.ashx"
    TIMEOUT = 30
    TOKEN_PARAM = "l10n_cl_currency_rate_bcentral.bcentral_api_token"

    SERIES_MAP = {
        "UF": "F073.UFF.PRE.Z.D",
        "USD": "F073.TCO.PRE.Z.D",
        "EUR": "F072.CLP.EUR.N.O.D",
        "UTM": "F073.UTR.PRE.Z.M",
    }

    @classmethod
    def _fetch(cls, token, series_id, start_date, end_date):
        if not token:
            raise ValueError("Banco Central API token is not configured")
        query = urllib.parse.urlencode(
            {
                "token": token,
                "function": "GetSeries",
                "timeseries": series_id,
                "firstdate": start_date.isoformat(),
                "lastdate": end_date.isoformat(),
            }
        )
        request = urllib.request.Request(
            f"{cls.BASE_URL}?{query}",
            headers={"Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=cls.TIMEOUT) as response:
                charset = response.headers.get_content_charset()
                response_body = cls._decode_response_body(response.read(), charset)
                try:
                    payload = json.loads(response_body)
                except json.JSONDecodeError:
                    raise ValueError(
                        "Banco Central returned a non-JSON response "
                        f"(Content-Type: {response.headers.get_content_type()})"
                    ) from None
        except urllib.error.HTTPError as exc:
            raise ValueError(f"Banco Central HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                detail = "timeout"
            else:
                detail = "connection error"
            raise ValueError(f"Banco Central {detail}") from None
        except ValueError:
            raise
        except Exception as exc:
            # Avoid echoing exception text: libraries may include the full URL,
            # which contains the personal API token as a query parameter.
            raise ValueError(
                f"Banco Central request failed ({type(exc).__name__})"
            ) from None

        if not isinstance(payload, dict) or payload.get("Codigo") not in (0, "0"):
            raise ValueError(
                "Banco Central rejected the request or returned an invalid response"
            )
        series = payload.get("Series")
        if not isinstance(series, dict) or series.get("seriesId") != series_id:
            raise ValueError(
                "Banco Central response does not match the requested series"
            )
        observations = series.get("Obs")
        if not isinstance(observations, list):
            raise ValueError("Banco Central response has no observations list")
        return observations

    @staticmethod
    def _decode_response_body(body, declared_charset=None):
        """Decode BDE responses using their declared charset or safe fallbacks."""
        encodings = (declared_charset, "utf-8-sig", "cp1252", "iso-8859-1")
        tried = set()
        for encoding in encodings:
            if not encoding or encoding.lower() in tried:
                continue
            tried.add(encoding.lower())
            try:
                return body.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                continue
        raise ValueError("Banco Central response has an unsupported text encoding")

    @classmethod
    def fetch_range(cls, currency_code, start_date, end_date, token):
        """Return normalized ``date``, ``value`` and ``status`` observations."""
        series_id = cls.SERIES_MAP.get(currency_code)
        if not series_id:
            raise ValueError(f"Unsupported Banco Central currency: {currency_code}")
        start_date = cls._to_date(start_date)
        end_date = cls._to_date(end_date)
        if end_date < start_date:
            raise ValueError("Banco Central date range is invalid")

        # A monthly series is indexed at the first day of the month. Expand the
        # request start to that index date when a daily document date is given.
        if currency_code == "UTM":
            start_date = start_date.replace(day=1)

        observations = cls._fetch(token, series_id, start_date, end_date)
        records = []
        for observation in observations:
            if not isinstance(observation, dict):
                raise ValueError("Banco Central returned a malformed observation")
            observation_date = cls._parse_observation_date(
                observation.get("indexDateString")
            )
            status = observation.get("statusCode")
            if status == "ND":
                records.append(
                    {"date": observation_date, "value": None, "status": "no_data"}
                )
                continue
            if status != "OK":
                raise ValueError(
                    "Banco Central returned an unsupported observation status"
                )
            value = cls._parse_value(observation.get("value"))
            records.append({"date": observation_date, "value": value, "status": "ok"})
        return records

    @classmethod
    def _parse_value(cls, raw_value):
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            raise ValueError("Banco Central returned a non-numeric rate") from None
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Banco Central returned a non-positive rate")
        return value

    @staticmethod
    def _parse_observation_date(raw_date):
        try:
            return datetime.strptime(raw_date, "%d-%m-%Y").date()
        except (TypeError, ValueError):
            raise ValueError(
                "Banco Central returned an invalid observation date"
            ) from None

    @staticmethod
    def _to_date(value):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value))

    @classmethod
    def test_connection(cls, token):
        """Make a small known-series request to validate token and response."""
        today = date.today()
        start = today.replace(day=1)
        cls.fetch_range("USD", start, today, token)
        return True
