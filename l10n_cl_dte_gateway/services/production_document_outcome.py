# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Validate parsed production responses for a known single-document request.

Callers must retain raw evidence before invoking this policy. This service does
not reparse XML or recover information discarded by the transport parser.
"""

import re
from collections.abc import Mapping

from odoo.exceptions import ValidationError
from odoo.tools.translate import LazyTranslate

from .production_response_parser import MAX_RESPONSE_BYTES

_lt = LazyTranslate(__name__, default_lang="en_US")
NATIONAL_DOCUMENT_CODES = frozenset({"33", "34", "52", "56", "61"})


class ProductionDocumentOutcome:
    """Dormant policy; transport activation belongs to the submission workflow."""

    def _validate_result(self, result, document_code):
        if (
            not isinstance(document_code, str)
            or document_code not in NATIONAL_DOCUMENT_CODES
        ):
            raise ValidationError(_lt("Unsupported national DTE document type."))
        if not isinstance(result, Mapping) or result.get("is_simulated") is not False:
            raise ValidationError(_lt("A non-simulated SII result is required."))
        raw = result.get("raw_response")
        if not isinstance(raw, bytes) or not raw or len(raw) > MAX_RESPONSE_BYTES:
            raise ValidationError(_lt("The raw SII response is missing or invalid."))

    def _validate_track(self, track_id):
        if (
            not isinstance(track_id, str)
            or not re.fullmatch(r"[0-9]{1,10}", track_id)
            or int(track_id) <= 0
        ):
            raise ValidationError(_lt("SII returned an invalid TrackID."))

    def normalize_upload(self, result, *, document_code):
        self._validate_result(result, document_code)
        state = result.get("state")
        code = result.get("transport_code")
        track = result.get("track_id")
        if state == "outcome_unknown" and code is False and track is False:
            return dict(result)
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{1,2}", code):
            raise ValidationError(_lt("SII returned an invalid upload status."))
        expected = "submitted" if code == "0" else "rejected"
        if state != expected or (expected == "rejected" and track is not False):
            raise ValidationError(_lt("The SII upload receipt is inconsistent."))
        if expected == "submitted":
            self._validate_track(track)
        return dict(result)

    def normalize_status(self, result, *, document_code, track_id):
        self._validate_result(result, document_code)
        self._validate_track(track_id)
        if result.get("track_id") != track_id:
            raise ValidationError(_lt("The SII result belongs to a different TrackID."))
        code = result.get("transport_code")
        if not isinstance(code, str) or not code or code != code.strip():
            raise ValidationError(_lt("SII returned an invalid query status."))
        rows = result.get("counts")
        if not isinstance(rows, list):
            raise ValidationError(_lt("The SII document counters are malformed."))
        if code == "EPR":
            state, observation = self._document_state(rows, document_code)
        else:
            state = (
                "rejected"
                if code in {"RSC", "RFR", "RCT"}
                else "processing"
                if code in {"SOK", "CRT", "FOK", "PDR", "PRD", "REC"}
                else "outcome_unknown"
            )
            observation = False
        normalized = {**result, "state": state}
        if observation:
            message = str(
                _lt("SII accepted the document with observations (REPAROS=1).")
            )
            previous = result.get("error_summary")
            normalized["error_summary"] = (
                f"{previous}\n{message}" if previous else message
            )
        return normalized

    def _document_state(self, rows, document_code):
        if not rows:
            return "processing", False
        if (
            len(rows) != 1
            or not isinstance(rows[0], Mapping)
            or rows[0].get("TIPO_DOCTO") != document_code
        ):
            raise ValidationError(_lt("Expected exactly one matching DTE counter row."))
        row = rows[0]
        fields = ("INFORMADOS", "ACEPTADOS", "RECHAZADOS", "REPAROS")
        if any(type(row.get(key)) is not int or row[key] < 0 for key in fields):
            raise ValidationError(
                _lt("SII document counters must be nonnegative integers.")
            )
        if row["INFORMADOS"] != 1 or sum(row[key] for key in fields[1:]) != 1:
            raise ValidationError(
                _lt("The SII single-document counters do not reconcile.")
            )
        return ("rejected" if row["RECHAZADOS"] else "accepted"), bool(row["REPAROS"])
