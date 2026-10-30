# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from lxml import etree

from odoo.tests import TransactionCase, tagged

from ..services.sii_client import (
    SiiDteClient,
    SiiTransportError,
    _invoice_response_status,
)


@tagged("post_install", "-at_install")
class TestSiiDteClient(TransactionCase):
    def setUp(self):
        super().setUp()
        self.company = SimpleNamespace(
            l10n_cl_dte_environment="SIITEST",
            vat="76543210-3",
            website="https://issuer.example",
        )
        self.certificate = SimpleNamespace(subject_serial_number="76543210-3")
        self.session = Mock()
        self.client = SiiDteClient(self.company, self.certificate, session=self.session)

    def test_invoice_upload_returns_track_id_and_uses_soap_endpoint(self):
        response = Mock(
            status_code=200,
            content=b"<html><STATUS>0</STATUS><TRACKID>1234567890</TRACKID></html>",
        )
        self.session.post.return_value = response
        with patch.object(self.client, "_get_token", return_value="test-token"):
            result = self.client.send(b"<EnvioDTE/>", "envio.xml", "33")

        self.assertEqual(result["track_id"], "1234567890")
        self.assertEqual(result["status"], "ask_for_status")
        args, kwargs = self.session.post.call_args
        self.assertEqual(args[0], "https://maullin.sii.cl/cgi_dte/UPL/DTEUpload")
        self.assertEqual(kwargs["headers"]["Cookie"], "TOKEN=test-token")
        self.assertEqual(kwargs["data"]["rutCompany"], "76543210")
        self.assertEqual(kwargs["files"]["archivo"][0], "envio.xml")

    def test_public_auth_token_can_use_boleta_rest_authentication(self):
        with patch.object(self.client, "_get_token", return_value="rest-token") as get:
            token = self.client.get_auth_token(rest=True, refresh=True)

        self.assertEqual(token, "rest-token")
        get.assert_called_once_with(refresh=True, rest=True)

    def test_boleta_upload_refreshes_explicit_401_once(self):
        unauthorized = Mock(status_code=401)
        accepted = Mock(status_code=200)
        accepted.json.return_value = {"trackid": 123456789012345, "estado": "REC"}
        self.session.post.side_effect = [unauthorized, accepted]
        with patch.object(
            self.client, "_get_token", side_effect=["stale-token", "fresh-token"]
        ) as get_token:
            result = self.client.send(b"<EnvioBOLETA/>", "boleta.xml", "39")

        self.assertEqual(get_token.call_count, 2)
        self.assertEqual(result["track_id"], "123456789012345")
        self.assertEqual(self.session.post.call_count, 2)
        self.assertEqual(
            self.session.post.call_args.kwargs["headers"]["User-Agent"],
            "Mozilla/4.0 ( compatible; PROG 1.0; Windows NT)",
        )

    def test_exempt_boleta_type_41_uses_rest_send_and_status(self):
        sent = Mock(status_code=200)
        sent.json.return_value = {"trackid": "123456789012345", "estado": "REC"}
        status = Mock(status_code=200)
        status.json.return_value = {
            "estado": "SOK",
            "estadistica": [
                {"tipo": 41, "informados": 1, "aceptados": 1, "reparos": 0}
            ],
        }
        self.session.post.return_value = sent
        self.session.get.return_value = status

        with patch.object(self.client, "_get_token", return_value="test-token"):
            result = self.client.send(b"<EnvioBOLETA/>", "exempt.xml", "41")
            checked = self.client.check_status(result["track_id"], "41", 700)

        self.assertEqual(result["track_id"], "123456789012345")
        self.assertEqual(checked["status"], "accepted")
        self.assertEqual(
            self.session.post.call_args.args[0],
            "https://pangal.sii.cl/recursos/v1/boleta.electronica.envio",
        )
        self.assertIn("76543210-3-123456789012345", self.session.get.call_args.args[0])

    def test_invoice_upload_refreshes_explicit_401_once(self):
        unauthorized = Mock(status_code=401)
        accepted = Mock(
            status_code=200,
            content=b"<html><STATUS>0</STATUS><TRACKID>1234567890</TRACKID></html>",
        )
        self.session.post.side_effect = [unauthorized, accepted]
        with patch.object(
            self.client, "_get_token", side_effect=["stale-token", "fresh-token"]
        ) as get_token:
            result = self.client.send(b"<EnvioDTE/>", "envio.xml", "33")

        self.assertEqual(get_token.call_count, 2)
        self.assertEqual(result["track_id"], "1234567890")
        self.assertEqual(self.session.post.call_count, 2)

    def test_invoice_upload_rejects_nonzero_sii_status_even_with_track_id(self):
        self.session.post.return_value = Mock(
            status_code=200,
            content=(
                b"<html><STATUS>3</STATUS><TRACKID>1234567890</TRACKID>"
                b"<GLOSA>Sobre rechazado</GLOSA></html>"
            ),
        )
        with patch.object(self.client, "_get_token", return_value="test-token"):
            result = self.client.send(b"<EnvioDTE/>", "envio.xml", "33")

        self.assertEqual(result["track_id"], "1234567890")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["diagnostic"], "Sobre rechazado")

    def test_invoice_upload_requires_status_and_track_id_for_receipt(self):
        for response_body in (
            b"<html><TRACKID>1234567890</TRACKID></html>",
            b"<html><STATUS>0</STATUS></html>",
        ):
            with self.subTest(response_body=response_body):
                self.session.post.return_value = Mock(
                    status_code=200, content=response_body
                )
                with patch.object(self.client, "_get_token", return_value="test-token"):
                    with self.assertRaises(SiiTransportError) as error:
                        self.client.send(b"<EnvioDTE/>", "envio.xml", "33")
                self.assertTrue(error.exception.outcome_unknown)

    def test_invoice_upload_rejects_malformed_track_id_as_uncertain(self):
        for track_id in ("12345", "12345678901"):
            with self.subTest(track_id=track_id):
                self.session.post.reset_mock()
                self.session.post.return_value = Mock(
                    status_code=200,
                    content=(
                        f"<html><STATUS>0</STATUS><TRACKID>{track_id}</TRACKID></html>"
                    ).encode(),
                )
                with patch.object(self.client, "_get_token", return_value="token"):
                    with self.assertRaises(SiiTransportError) as error:
                        self.client.send(b"<EnvioDTE/>", "envio.xml", "33")

                self.assertTrue(error.exception.outcome_unknown)
                self.assertEqual(error.exception.track_id, track_id)
                self.assertEqual(self.session.post.call_count, 1)

    def test_invoice_status_rejects_malformed_track_id_before_network(self):
        for track_id in ("12345", "12345678901"):
            with self.subTest(track_id=track_id):
                with (
                    patch.object(self.client, "_get_token") as get_token,
                    patch.object(self.client, "soap_factory") as soap_factory,
                ):
                    with self.assertRaisesRegex(SiiTransportError, "10 positions"):
                        self.client.check_status(track_id, "33", 100)

                get_token.assert_not_called()
                soap_factory.assert_not_called()

    def test_invoice_upload_refreshes_token_for_explicit_status_five(self):
        self.session.post.side_effect = [
            Mock(
                status_code=200,
                content=b"<html><STATUS>5</STATUS></html>",
            ),
            Mock(
                status_code=200,
                content=b"<html><STATUS>0</STATUS><TRACKID>0987654321</TRACKID></html>",
            ),
        ]
        with patch.object(
            self.client, "_get_token", side_effect=["expired", "fresh"]
        ) as get_token:
            result = self.client.send(b"<EnvioDTE/>", "envio.xml", "33")

        self.assertEqual(get_token.call_count, 2)
        self.assertEqual(self.session.post.call_count, 2)
        self.assertEqual(result["track_id"], "0987654321")
        self.assertEqual(result["status"], "ask_for_status")

    def test_uncertain_invoice_timeout_is_not_retried(self):
        self.session.post.side_effect = requests.Timeout("timeout")
        with patch.object(self.client, "_get_token", return_value="test-token"):
            with self.assertRaises(SiiTransportError) as error:
                self.client.send(b"<EnvioDTE/>", "envio.xml", "33")

        self.assertTrue(error.exception.outcome_unknown)
        self.assertEqual(self.session.post.call_count, 1)

    def test_boleta_status_matches_document_type_and_folio(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "estado": "EPR",
            "detalle_rep_rech": [
                {"tipo": 39, "folio": 99, "estado": "RCH"},
                {"tipo": 39, "folio": 100, "estado": "DOK"},
            ],
        }
        self.session.get.return_value = response
        with patch.object(self.client, "_get_token", return_value="test-token"):
            result = self.client.check_status("123456789012345", "39", 100)

        self.assertEqual(result["status"], "accepted")
        self.assertIn("76543210-3-123456789012345", self.session.get.call_args.args[0])

    def test_boleta_upload_requires_rec_and_track_id(self):
        responses = (
            {"trackid": 123456789012345},
            {"trackid": 123456789012345, "estado": "SOK"},
            {"estado": "REC"},
        )
        for payload in responses:
            with self.subTest(payload=payload):
                response = Mock(status_code=200)
                response.json.return_value = payload
                self.session.post.return_value = response
                with patch.object(self.client, "_get_token", return_value="token"):
                    with self.assertRaises(SiiTransportError) as error:
                        self.client.send(b"<EnvioBOLETA/>", "boleta.xml", "39")

                self.assertTrue(error.exception.outcome_unknown)
                self.assertEqual(
                    error.exception.track_id,
                    "123456789012345" if "trackid" in payload else None,
                )

    def test_boleta_upload_rejects_malformed_track_id_as_uncertain(self):
        for track_id in ("678", "12345678901234", "12345678901234X", True):
            with self.subTest(track_id=track_id):
                self.session.post.reset_mock()
                response = Mock(status_code=200)
                response.json.return_value = {"trackid": track_id, "estado": "REC"}
                self.session.post.return_value = response
                with patch.object(self.client, "_get_token", return_value="token"):
                    with self.assertRaises(SiiTransportError) as error:
                        self.client.send(b"<EnvioBOLETA/>", "boleta.xml", "39")

                self.assertTrue(error.exception.outcome_unknown)
                self.assertEqual(
                    error.exception.track_id,
                    track_id if isinstance(track_id, str) else None,
                )
                self.assertEqual(self.session.post.call_count, 1)

    def test_boleta_status_rejects_malformed_track_id_before_network(self):
        for track_id in ("678", "12345678901234X", True):
            with self.subTest(track_id=track_id):
                with patch.object(self.client, "_get_token") as get_token:
                    with self.assertRaisesRegex(SiiTransportError, "15 digits"):
                        self.client.check_status(track_id, "39", 100)

                get_token.assert_not_called()
                self.session.get.assert_not_called()

    def test_boleta_upload_malformed_json_is_uncertain_and_not_retried(self):
        response = Mock(status_code=200)
        response.json.side_effect = ValueError("invalid json")
        self.session.post.return_value = response
        with patch.object(self.client, "_get_token", return_value="token"):
            with self.assertRaises(SiiTransportError) as error:
                self.client.send(b"<EnvioBOLETA/>", "boleta.xml", "39")

        self.assertTrue(error.exception.outcome_unknown)
        self.assertEqual(self.session.post.call_count, 1)

    def test_boleta_status_requires_known_envelope_state(self):
        for state in (None, "UNKNOWN"):
            with self.subTest(state=state):
                response = Mock(status_code=200)
                response.json.return_value = {
                    "estado": state,
                    "estadistica": [{"tipo": 39, "informados": 1, "aceptados": 1}],
                }
                self.session.get.return_value = response
                with patch.object(self.client, "_get_token", return_value="token"):
                    with self.assertRaises(SiiTransportError):
                        self.client.check_status("123456789012345", "39", 100)

    def test_boleta_status_uses_only_matching_type_statistics(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "estado": "EPR",
            "estadistica": [
                {"tipo": 41, "informados": 1, "aceptados": 1, "reparos": 0},
                {"tipo": 39, "informados": 1, "aceptados": 0, "reparos": 1},
            ],
        }
        self.session.get.return_value = response
        with patch.object(self.client, "_get_token", return_value="token"):
            result = self.client.check_status("123456789012345", "39", 100)

        self.assertEqual(result["status"], "objected")

    def test_invoice_response_maps_all_terminal_and_pending_states(self):
        ns = "http://www.sii.cl/XMLSchema"
        self.assertEqual(
            _invoice_response_status(
                etree.fromstring(f'<R xmlns="{ns}"><ESTADO>PDR</ESTADO></R>'),
                "PDR",
            ),
            "ask_for_status",
        )
        self.assertEqual(
            _invoice_response_status(
                etree.fromstring(
                    f'<R xmlns="{ns}"><TIPO_DOCTO>33</TIPO_DOCTO>'
                    "<INFORMADOS>1</INFORMADOS><ACEPTADOS>1</ACEPTADOS>"
                    "<RECHAZADOS>0</RECHAZADOS><REPAROS>0</REPAROS></R>"
                ),
                "EPR",
            ),
            "accepted",
        )
        self.assertEqual(
            _invoice_response_status(
                etree.fromstring(
                    f'<R xmlns="{ns}"><TIPO_DOCTO>33</TIPO_DOCTO>'
                    "<INFORMADOS>1</INFORMADOS><ACEPTADOS>0</ACEPTADOS>"
                    "<RECHAZADOS>1</RECHAZADOS><REPAROS>0</REPAROS></R>"
                ),
                "EPR",
            ),
            "rejected",
        )
        self.assertEqual(
            _invoice_response_status(
                etree.fromstring(
                    f'<R xmlns="{ns}"><TIPO_DOCTO>33</TIPO_DOCTO>'
                    "<INFORMADOS>1</INFORMADOS><ACEPTADOS>0</ACEPTADOS>"
                    "<RECHAZADOS>0</RECHAZADOS><REPAROS>1</REPAROS></R>"
                ),
                "EPR",
            ),
            "objected",
        )
        mixed = etree.fromstring(
            f'<R xmlns="{ns}"><TIPO_DOCTO>33</TIPO_DOCTO>'
            "<INFORMADOS>2</INFORMADOS><ACEPTADOS>1</ACEPTADOS>"
            "<RECHAZADOS>1</RECHAZADOS><REPAROS>0</REPAROS></R>"
        )
        self.assertEqual(_invoice_response_status(mixed, "EPR"), "ask_for_status")
        all_accepted_batch = etree.fromstring(
            f'<R xmlns="{ns}"><TIPO_DOCTO>33</TIPO_DOCTO>'
            "<INFORMADOS>2</INFORMADOS><ACEPTADOS>2</ACEPTADOS>"
            "<RECHAZADOS>0</RECHAZADOS><REPAROS>0</REPAROS></R>"
        )
        self.assertEqual(
            _invoice_response_status(all_accepted_batch, "EPR"), "ask_for_status"
        )
        self.assertEqual(_invoice_response_status(mixed, "NEW-CODE"), "ask_for_status")
        self.assertEqual(
            _invoice_response_status(
                etree.fromstring(f'<R xmlns="{ns}"><ESTADO>RCH</ESTADO></R>'),
                "RCH",
            ),
            "rejected",
        )

    def test_invoice_status_refreshes_invalid_token_once(self):
        first = Mock()
        first.service.getEstUp.return_value = (
            b"<R><RESP_HDR><ESTADO>5</ESTADO></RESP_HDR></R>"
        )
        second = Mock()
        second.service.getEstUp.return_value = (
            b"<R><RESP_HDR><TRACKID>1234567890</TRACKID><ESTADO>EPR</ESTADO>"
            b"<GLOSA>Envio Procesado</GLOSA><NUM_ATENCION>AT-42</NUM_ATENCION>"
            b"</RESP_HDR><RESP_BODY><TIPO_DOCTO>33</TIPO_DOCTO>"
            b"<ACEPTADOS>1</ACEPTADOS><RECHAZADOS>0</RECHAZADOS>"
            b"<REPAROS>0</REPAROS><INFORMADOS>1</INFORMADOS></RESP_BODY></R>"
        )
        self.client.soap_factory = Mock(side_effect=[first, second])
        with patch.object(
            self.client, "_get_token", side_effect=["expired", "fresh"]
        ) as get_token:
            result = self.client.check_status("1234567890", "33", 100)

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["sii_status_code"], "EPR")
        self.assertEqual(result["sii_attention_number"], "AT-42")
        self.assertEqual(result["sii_document_type"], "33")
        self.assertEqual(result["sii_informed_count"], 1)
        self.assertEqual(result["sii_accepted_count"], 1)
        self.assertEqual(result["sii_rejected_count"], 0)
        self.assertEqual(result["sii_objection_count"], 0)
        self.assertEqual(get_token.call_count, 2)
        self.assertEqual(self.client.soap_factory.call_count, 2)

    def test_invoice_status_rejects_response_for_a_different_track_id(self):
        service = Mock()
        service.service.getEstUp.return_value = (
            b"<R><RESP_HDR><TRACKID>9999999999</TRACKID><ESTADO>PDR</ESTADO>"
            b"</RESP_HDR></R>"
        )
        self.client.soap_factory = Mock(return_value=service)
        with patch.object(self.client, "_get_token", return_value="token"):
            with self.assertRaisesRegex(SiiTransportError, "different Track ID"):
                self.client.check_status("1234567890", "33", 100)
        self.assertEqual(self.client.soap_factory.call_count, 1)

    def test_invoice_status_does_not_apply_another_document_type_to_the_move(self):
        service = Mock()
        service.service.getEstUp.return_value = (
            b"<R><RESP_HDR><TRACKID>1234567890</TRACKID><ESTADO>EPR</ESTADO>"
            b"</RESP_HDR><RESP_BODY><TIPO_DOCTO>56</TIPO_DOCTO>"
            b"<INFORMADOS>1</INFORMADOS><ACEPTADOS>1</ACEPTADOS>"
            b"<RECHAZADOS>0</RECHAZADOS><REPAROS>0</REPAROS></RESP_BODY></R>"
        )
        self.client.soap_factory = Mock(return_value=service)
        with patch.object(self.client, "_get_token", return_value="token"):
            result = self.client.check_status("1234567890", "33", 100)

        self.assertEqual(result["status"], "ask_for_status")
        self.assertEqual(result["sii_document_type"], "56")
        self.assertEqual(
            result["diagnostic"],
            "The SII response document type does not match the invoice.",
        )

    def test_invoice_status_preserves_query_error_codes(self):
        service = Mock()
        service.service.getEstUp.return_value = (
            b"<R><RESP_HDR><ESTADO>-11</ESTADO><GLOSA>Error de consulta</GLOSA>"
            b"<ERR_CODE>1</ERR_CODE><SQL_CODE>0</SQL_CODE><SRV_CODE>0</SRV_CODE>"
            b"<NUM_ATENCION>AT-ERR</NUM_ATENCION></RESP_HDR></R>"
        )
        self.client.soap_factory = Mock(return_value=service)
        with patch.object(self.client, "_get_token", return_value="token"):
            result = self.client.check_status("1234567890", "33", 100)

        self.assertEqual(result["status"], "ask_for_status")
        self.assertEqual(result["sii_status_code"], "-11")
        self.assertEqual(result["sii_error_code"], "1")
        self.assertEqual(result["sii_sql_code"], "0")
        self.assertEqual(result["sii_server_code"], "0")
        self.assertEqual(result["sii_attention_number"], "AT-ERR")

    def test_invoice_status_retries_token_code_without_estado(self):
        first = Mock()
        first.service.getEstUp.return_value = b"<R><TOKEN>001</TOKEN></R>"
        second = Mock()
        second.service.getEstUp.return_value = b"<R><TOKEN>002</TOKEN></R>"
        self.client.soap_factory = Mock(side_effect=[first, second])
        with patch.object(
            self.client, "_get_token", side_effect=["old", "new"]
        ) as get_token:
            with self.assertRaisesRegex(SiiTransportError, "TOKEN=002") as raised:
                self.client.check_status("1234567890", "33", 100)

        self.assertEqual(get_token.call_count, 2)
        self.assertEqual(raised.exception.response_metadata["sii_token_code"], "002")

    def test_signed_token_signature_and_digest_match_serialized_xml(self):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = private_key.public_key().public_numbers()

        class TestCertificate:
            subject_serial_number = "76543210-3"

            def _sign(self, data, hashing_algorithm, formatting):
                signed = private_key.sign(data, padding.PKCS1v15(), hashes.SHA1())
                return base64.b64encode(signed)

            def _get_public_key_numbers_bytes(self, formatting):
                return (
                    base64.b64encode(
                        numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
                    ),
                    base64.b64encode(
                        numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
                    ),
                )

            def _get_der_certificate_bytes(self, formatting):
                return base64.b64encode(b"synthetic-certificate")

        self.client.certificate = TestCertificate()
        from ..services.sii_client import _c14n

        document = etree.fromstring(self.client._signed_token("SEED-123"))
        signature = document.find("{http://www.w3.org/2000/09/xmldsig#}Signature")
        signed_info = signature.find("{http://www.w3.org/2000/09/xmldsig#}SignedInfo")
        signature_bytes = base64.b64decode(
            signature.find("{http://www.w3.org/2000/09/xmldsig#}SignatureValue").text
        )
        private_key.public_key().verify(
            signature_bytes,
            _c14n(signed_info),
            padding.PKCS1v15(),
            hashes.SHA1(),
        )
        unsigned = deepcopy(document)
        unsigned.remove(unsigned.find("{http://www.w3.org/2000/09/xmldsig#}Signature"))
        expected = base64.b64encode(hashlib.sha1(_c14n(unsigned)).digest()).decode()
        digest = signed_info.find(
            ".//{http://www.w3.org/2000/09/xmldsig#}DigestValue"
        ).text
        self.assertEqual(digest, expected)
