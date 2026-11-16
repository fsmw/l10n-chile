# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from copy import deepcopy

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from ..services import production_document_outcome
from ..services.production_response_parser import parse_status, parse_upload
from .test_certification_transport import soap_response, upload_response


@tagged("post_install", "-at_install")
class TestProductionDocumentOutcome(TransactionCase):
    def setUp(self):
        super().setUp()
        self.policy = production_document_outcome.ProductionDocumentOutcome()

    def status(self, code="33", status="EPR", accepted=1, rejected=0, observed=0):
        payload = (
            "<RESPUESTA><RESP_HDR><TRACKID>123</TRACKID>"
            f"<ESTADO>{status}</ESTADO></RESP_HDR><RESP_BODY>"
            f"<TIPO_DOCTO>{code}</TIPO_DOCTO><INFORMADOS>1</INFORMADOS>"
            f"<ACEPTADOS>{accepted}</ACEPTADOS><RECHAZADOS>{rejected}</RECHAZADOS>"
            f"<REPAROS>{observed}</REPAROS></RESP_BODY></RESPUESTA>"
        )
        result = parse_status(soap_response("getEstUp", payload), "123")
        return {**result, "is_simulated": False}

    def normalize(self, result, code="33", track="123"):
        return self.policy.normalize_status(result, document_code=code, track_id=track)

    def test_upload_receipt_is_not_fiscal_acceptance(self):
        for status, state in (("0", "submitted"), ("7", "rejected")):
            with self.subTest(status=status):
                raw = upload_response(status=status)
                result = {
                    **parse_upload(raw, "12345678-5", "12345678-5"),
                    "is_simulated": False,
                }
                before = deepcopy(result)
                actual = self.policy.normalize_upload(result, document_code="33")
                self.assertEqual(actual["state"], state)
                self.assertIs(actual["raw_response"], raw)
                self.assertEqual(result, before)

    def test_valid_single_document_outcomes_for_national_codes(self):
        for code in ("33", "34", "52", "56", "61"):
            for accepted, rejected, observed, state in (
                (1, 0, 0, "accepted"),
                (0, 1, 0, "rejected"),
                (0, 0, 1, "accepted"),
            ):
                with self.subTest(code=code, state=state, observed=observed):
                    result = self.status(
                        code, accepted=accepted, rejected=rejected, observed=observed
                    )
                    before = deepcopy(result)
                    actual = self.normalize(result, code)
                    self.assertEqual(actual["state"], state)
                    self.assertEqual(bool(actual.get("error_summary")), bool(observed))
                    self.assertEqual(actual["raw_response"], result["raw_response"])
                    self.assertEqual(result, before)

    def test_unknown_upload_is_never_promoted(self):
        result = {
            "state": "outcome_unknown",
            "track_id": False,
            "transport_code": False,
            "raw_response": b"unrecognized retained response",
            "is_simulated": False,
        }
        actual = self.policy.normalize_upload(result, document_code="33")
        self.assertEqual(actual, result)

    def test_observation_preserves_existing_diagnostic_and_evidence(self):
        result = {
            **self.status(accepted=0, observed=1),
            "error_summary": "Existing diagnostic",
        }
        before = deepcopy(result)
        actual = self.normalize(result)
        self.assertIn("Existing diagnostic", actual["error_summary"])
        self.assertIn("REPAROS=1", actual["error_summary"])
        self.assertIs(actual["raw_response"], result["raw_response"])
        self.assertEqual(result, before)

    def test_processing_unknown_and_whole_envelope_rejection(self):
        for code, state in (
            ("RSC", "rejected"),
            ("RFR", "rejected"),
            ("RCT", "rejected"),
            ("SOK", "processing"),
            ("CRT", "processing"),
            ("FOK", "processing"),
            ("PDR", "processing"),
            ("PRD", "processing"),
            ("REC", "processing"),
            ("NEW", "outcome_unknown"),
        ):
            with self.subTest(code=code):
                result = self.status(status=code)
                self.assertEqual(self.normalize(result)["state"], state)

    def test_missing_counters_cannot_infer_acceptance(self):
        result = {**self.status(), "counts": [], "state": "accepted"}
        self.assertEqual(self.normalize(result)["state"], "processing")

    def test_wrong_or_duplicate_document_counts_are_rejected(self):
        result = self.status()
        for rows in (
            [{**result["counts"][0], "TIPO_DOCTO": "34"}],
            result["counts"] * 2,
            [{**result["counts"][0], "INFORMADOS": 2, "ACEPTADOS": 2}],
        ):
            with self.subTest(rows=rows), self.assertRaises(ValidationError):
                self.normalize({**result, "counts": rows})

    def test_counter_types_and_arithmetic_are_strict(self):
        result = self.status()
        for field in ("INFORMADOS", "ACEPTADOS", "RECHAZADOS", "REPAROS"):
            for value in (-1, True, "1", 1.0, None):
                with self.subTest(field=field, value=value):
                    row = {**result["counts"][0], field: value}
                    with self.assertRaises(ValidationError):
                        self.normalize({**result, "counts": [row]})
        row = {**result["counts"][0], "RECHAZADOS": 1}
        with self.assertRaises(ValidationError):
            self.normalize({**result, "counts": [row]})

    def test_missing_or_malformed_result_fields_are_rejected(self):
        result = self.status()
        for key in ("raw_response", "track_id", "transport_code", "is_simulated"):
            missing = {name: value for name, value in result.items() if name != key}
            with self.subTest(missing=key), self.assertRaises(ValidationError):
                self.normalize(missing)
        for key, value in (
            ("raw_response", b""),
            ("raw_response", "xml"),
            ("raw_response", b"x" * 2_000_001),
            ("is_simulated", True),
            ("is_simulated", 0),
            ("transport_code", None),
            ("counts", None),
            ("counts", [{}]),
            ("counts", [None]),
        ):
            with (
                self.subTest(key=key, value_type=type(value)),
                self.assertRaises(ValidationError),
            ):
                self.normalize({**result, key: value})
        for invalid in (None, [], "result"):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                self.normalize(invalid)

    def test_track_id_is_bound_to_the_request(self):
        for track in (None, "", "0", "-1", "9" * 11, 123, "999"):
            with self.subTest(track=track), self.assertRaises(ValidationError):
                self.normalize(self.status(), track=track)

    def test_unsupported_document_types_fail_closed(self):
        for code in (None, 33, "39", "41", "110", "111", "112"):
            with self.subTest(code=code), self.assertRaises(ValidationError):
                self.normalize(self.status(), code=code)

    def test_upload_cannot_forge_track_or_success_state(self):
        result = {
            **parse_upload(upload_response(), "12345678-5", "12345678-5"),
            "is_simulated": False,
        }
        for changes in (
            {"track_id": "0"},
            {"track_id": "bad"},
            {"transport_code": ""},
            {"transport_code": "100"},
            {"state": "accepted"},
            {"transport_code": "7"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.policy.normalize_upload({**result, **changes}, document_code="33")
