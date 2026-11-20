# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json
from unittest.mock import MagicMock, Mock, patch

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase

from ..services import boleta_rest
from ..services.boleta_rest import SiiBoletaRestClient, parse_status
from ..services.certification_auth import SiiTransportError

SEED = (
    b'<SII:RESPUESTA xmlns:SII="http://www.sii.cl/XMLSchema"><SII:RESP_BODY>'
    b"<SEMILLA>012345678</SEMILLA></SII:RESP_BODY><SII:RESP_HDR><ESTADO>00"
    b"</ESTADO></SII:RESP_HDR></SII:RESPUESTA>"
)
TOKEN = (
    b'<SII:RESPUESTA xmlns:SII="http://www.sii.cl/XMLSchema"><SII:RESP_BODY>'
    b"<TOKEN>ABC123TOKEN</TOKEN></SII:RESP_BODY><SII:RESP_HDR><ESTADO>00"
    b"</ESTADO></SII:RESP_HDR></SII:RESPUESTA>"
)


def _json(**values):
    return json.dumps(values).encode()


class TestBoletaRest(TransactionCase):
    """The SII boleta REST client, on answers shaped like the SII ones."""

    def setUp(self):
        super().setUp()
        boleta_rest._TOKENS.clear()
        self.company = Mock(vat="76086428-5")
        self.company.env.cr.dbname = "db"
        self.company.id = 1

    def _client(self, *answers):
        session = Mock()
        responses = []
        for status, content in answers:
            response = MagicMock()
            response.status_code = status
            response.raw.read.return_value = content
            responses.append(response)
        session.request.return_value.__enter__ = Mock(side_effect=responses)
        session.request.return_value.__exit__ = Mock(return_value=False)
        return SiiBoletaRestClient("production", session=session), session

    def _sign(self):
        return patch.object(
            boleta_rest.SignatureService,
            "sign_authentication_seed",
            return_value=b"<getToken/>",
        )

    def test_upload_authenticates_and_returns_the_track_id(self):
        client, session = self._client(
            (200, SEED),
            (200, TOKEN),
            (200, _json(trackid=4567, estado="REC")),
        )
        with self._sign():
            result = client.send_envelope(self.company, b"<EnvioBOLETA/>", "11111111-1")
        self.assertEqual(result["track_id"], "4567")
        calls = session.request.call_args_list
        self.assertEqual(
            calls[0].args[1],
            boleta_rest.HOSTS["production"]["api"] + "boleta.electronica.semilla",
        )
        upload = calls[2]
        self.assertEqual(upload.args[1], boleta_rest.HOSTS["production"]["send"])
        self.assertEqual(upload.kwargs["headers"]["Cookie"], "TOKEN=ABC123TOKEN")
        self.assertEqual(upload.kwargs["headers"]["User-Agent"], boleta_rest.USER_AGENT)
        self.assertEqual(upload.kwargs["data"]["rutCompany"], "76086428")

    def test_expired_token_is_refreshed_once(self):
        client, session = self._client(
            (200, SEED),
            (200, TOKEN),
            (401, b""),
            (200, SEED),
            (200, TOKEN),
            (200, _json(trackid=9, estado="REC")),
        )
        with self._sign():
            result = client.send_envelope(self.company, b"<EnvioBOLETA/>", "11111111-1")
        self.assertEqual(result["track_id"], "9")
        self.assertEqual(session.request.call_count, 6)

    def test_upload_without_track_id_is_an_uncertain_outcome(self):
        client, _session = self._client((200, SEED), (200, TOKEN), (200, _json()))
        with self._sign(), self.assertRaises(SiiTransportError) as caught:
            client.send_envelope(self.company, b"<EnvioBOLETA/>", "11111111-1")
        self.assertTrue(caught.exception.dispatched)

    def test_status_answers_are_mapped(self):
        accepted = _json(
            trackid=4567,
            estado="EPR",
            estadistica=[{"tipo": 39, "informados": 1, "aceptados": 1}],
        )
        observed = _json(
            trackid=4567,
            estado="EPR",
            detalle_rep_rech=[{"tipo": 39, "folio": 5, "estado": "RPR"}],
        )
        rejected = _json(
            trackid=4567,
            estado="EPR",
            detalle_rep_rech=[{"tipo": 39, "folio": 5, "estado": "RCH"}],
        )
        self.assertEqual(parse_status(accepted, "4567", "39")["state"], "accepted")
        observed_result = parse_status(observed, "4567", "39")
        self.assertEqual(observed_result["state"], "accepted")
        self.assertTrue(observed_result["error_summary"])
        self.assertEqual(parse_status(rejected, "4567", "39")["state"], "rejected")
        self.assertEqual(
            parse_status(_json(trackid=4567, estado="REC"), "4567", "39")["state"],
            "processing",
        )
        with self.assertRaises(ValidationError):
            parse_status(_json(trackid=1), "4567", "39")

    def test_demo_mode_has_no_boleta_service(self):
        with self.assertRaises(ValidationError):
            SiiBoletaRestClient("demo")
