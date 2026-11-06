import base64
import hashlib
from unittest.mock import Mock, patch

from odoo.exceptions import UserError

from odoo.addons.l10n_cl_dte_gateway.services.certification_auth import (
    SiiTransportError,
)
from odoo.addons.l10n_cl_dte_gateway.services.certification_envelope import (
    CertificationSiiEnvelopeGateway,
)
from odoo.addons.l10n_cl_dte_signing.tests.common import SigningTestCase


class TestCertificationEnvelopeUpload(SigningTestCase):
    def setUp(self):
        super().setUp()
        self.company = self.company.with_user(self.dte_manager)
        self.company.sudo().write({"dte_sii_environment": "certification"})
        self.company.partner_id.sudo().write({"vat": "12345678-5"})
        self.create_signing_profile(environment="certification")
        self.envelope = (
            b'<EnvioDTE xmlns="http://www.sii.cl/SiiDte"><SetDTE>'
            b"<Caratula><RutEnvia>12.345.678-5</RutEnvia></Caratula>"
            b"</SetDTE></EnvioDTE>"
        )

    def make_gateway(self, response):
        authentication = Mock()
        authentication.token.return_value = "synthetic42"
        authentication.post.return_value = response
        authentication.soap.return_value = response
        authentication.session.cookies = Mock()
        factory = Mock(return_value=authentication)
        return CertificationSiiEnvelopeGateway(factory), authentication

    def create_submission(self, key):
        return (
            self.env["l10n_cl.sii.envelope.submission"]
            .with_user(self.dte_manager)
            ._create_from_workflow(
                {
                    "name": f"Synthetic envelope {key}",
                    "company_id": self.company.id,
                    "environment": "certification",
                    "request_xml": base64.b64encode(self.envelope),
                    "request_filename": "synthetic.xml",
                    "request_digest": hashlib.sha256(self.envelope).hexdigest(),
                    "idempotency_key": key,
                }
            )
        )

    def test_upload_uses_certification_auth_and_persists_track_id_evidence(self):
        response = (
            b"<RECEPCIONDTE><STATUS>0</STATUS>"
            b"<TRACKID>2638866436</TRACKID></RECEPCIONDTE>"
        )
        gateway, authentication = self.make_gateway(response)

        result = gateway.send_envelope(
            self.company, "certification", self.envelope, "synthetic-idempotency"
        )

        self.assertEqual(
            result,
            {
                "state": "submitted",
                "track_id": "2638866436",
                "raw_response": response,
                "is_simulated": False,
            },
        )
        authentication.token.assert_called_once_with(self.company)
        authentication.session.cookies.set.assert_called_once_with(
            "TOKEN", "synthetic42", domain="maullin.sii.cl", path="/"
        )
        args, kwargs = authentication.post.call_args
        self.assertEqual(args, ("/cgi_dte/UPL/DTEUpload",))
        self.assertTrue(kwargs["upload"])
        self.assertEqual(
            kwargs["data"],
            {
                "rutSender": "12345678",
                "dvSender": "5",
                "rutCompany": "12345678",
                "dvCompany": "5",
            },
        )
        self.assertEqual(kwargs["files"]["archivo"][1], self.envelope)
        authentication.close.assert_called_once()

    def test_nonzero_upload_status_is_a_known_failure_without_track_id(self):
        response = b"<RECEPCIONDTE><STATUS>7</STATUS></RECEPCIONDTE>"
        gateway, _authentication = self.make_gateway(response)

        result = gateway.send_envelope(
            self.company, "certification", self.envelope, "synthetic-idempotency"
        )

        self.assertEqual(result["state"], "error")
        self.assertFalse(result["track_id"])
        self.assertEqual(result["raw_response"], response)
        self.assertEqual(result["error_summary"], "SII upload status 7")

    def test_missing_track_id_after_success_is_ambiguous(self):
        gateway, authentication = self.make_gateway(
            b"<RECEPCIONDTE><STATUS>0</STATUS></RECEPCIONDTE>"
        )

        with self.assertRaises(UserError):
            gateway.send_envelope(
                self.company,
                "certification",
                self.envelope,
                "synthetic-idempotency",
            )

        authentication.close.assert_called_once()

    def test_non_certification_mode_is_rejected_before_authentication(self):
        auth_factory = Mock()
        gateway = CertificationSiiEnvelopeGateway(auth_factory)

        with self.assertRaises(UserError):
            gateway.send_envelope(
                self.company, "production", self.envelope, "synthetic-idempotency"
            )

        auth_factory.assert_not_called()

    def test_query_uses_company_track_id_and_returns_processing_state(self):
        response = (
            b'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
            b"<soap:Body><getEstUpReturn>"
            b"&lt;RESPUESTA&gt;&lt;RESP_HDR&gt;&lt;TRACKID&gt;123&lt;/TRACKID&gt;"
            b"&lt;ESTADO&gt;PDR&lt;/ESTADO&gt;&lt;GLOSA&gt;Envio en Proceso"
            b"&lt;/GLOSA&gt;&lt;/RESP_HDR&gt;&lt;/RESPUESTA&gt;"
            b"</getEstUpReturn></soap:Body></soap:Envelope>"
        )
        gateway, authentication = self.make_gateway(response)

        result = gateway.query_envelope(self.company, "certification", "123")

        self.assertEqual(result["state"], "processing")
        self.assertEqual(result["raw_response"], response)
        self.assertFalse(result["is_simulated"])
        authentication.soap.assert_called_once_with(
            "QueryEstUp",
            "getEstUp",
            {
                "Rut": "12345678",
                "Dv": "5",
                "TrackId": "123",
                "Token": "synthetic42",
            },
        )
        authentication.close.assert_called_once()

    def test_processed_query_preserves_repair_counts_in_attempt_summary(self):
        payload = (
            b"<RESPUESTA><RESP_HDR><TRACKID>123</TRACKID><ESTADO>EPR</ESTADO>"
            b"</RESP_HDR><RESP_BODY><TIPO_DOCTO>33</TIPO_DOCTO>"
            b"<INFORMADOS>2</INFORMADOS><ACEPTADOS>1</ACEPTADOS>"
            b"<RECHAZADOS>0</RECHAZADOS><REPAROS>1</REPAROS>"
            b"</RESP_BODY></RESPUESTA>"
        )
        from lxml import etree

        root = etree.fromstring(payload)
        state, summary = CertificationSiiEnvelopeGateway._map_query_state(root, "EPR")

        self.assertEqual(state, "result_pending")
        self.assertEqual(
            summary,
            "SII processed aggregate: 1 accepted, 0 rejected, 1 with repairs; "
            "per-document results require reconciliation",
        )

    def test_query_failure_does_not_change_known_submission_state(self):
        submission = self.create_submission("synthetic-query-error")
        submission._write_internal({"state": "submitted", "track_id": "123"})
        failure = b"<RESPUESTA><RESP_HDR><ESTADO>003</ESTADO></RESP_HDR></RESPUESTA>"

        with patch.object(
            CertificationSiiEnvelopeGateway,
            "query_envelope",
            return_value={
                "state": "error",
                "raw_response": failure,
                "error_summary": "SII query status 003",
                "is_simulated": False,
            },
        ):
            submission._job_query()

        self.assertEqual(submission.state, "submitted")
        self.assertEqual(submission.attempt_ids.state, "failed")
        self.assertEqual(submission.attempt_ids.error_summary, "SII query status 003")
        self.assertTrue(submission.last_checked_at)

    def test_upload_rejection_is_persisted_as_known_error_with_response(self):
        submission = self.create_submission("synthetic-upload-rejection")
        response = b"<RECEPCIONDTE><STATUS>7</STATUS></RECEPCIONDTE>"
        with patch.object(
            CertificationSiiEnvelopeGateway,
            "send_envelope",
            return_value={
                "state": "error",
                "track_id": False,
                "raw_response": response,
                "error_summary": "SII upload status 7",
                "is_simulated": False,
            },
        ):
            submission._job_send()

        self.assertEqual(submission.state, "error")
        self.assertFalse(submission.track_id)
        self.assertEqual(submission.attempt_ids.state, "failed")
        self.assertEqual(
            submission.attempt_ids.response_xml, base64.b64encode(response)
        )
        self.assertEqual(submission.attempt_ids.error_summary, "SII upload status 7")

    def test_submission_stores_track_id_and_does_not_resend_unknown_outcome(self):
        submission = self.create_submission("synthetic-envelope-submission")
        received = (
            b"<RECEPCIONDTE><STATUS>0</STATUS>"
            b"<TRACKID>12345678</TRACKID></RECEPCIONDTE>"
        )
        with patch.object(
            CertificationSiiEnvelopeGateway,
            "send_envelope",
            return_value={
                "state": "submitted",
                "track_id": "12345678",
                "raw_response": received,
                "is_simulated": False,
            },
        ) as send:
            submission._job_send()
            self.assertEqual(submission.state, "submitted")
            self.assertEqual(submission.track_id, "12345678")
            self.assertEqual(submission.attempt_ids.state, "completed")
            self.assertEqual(
                submission.attempt_ids.response_xml, base64.b64encode(received)
            )

        with patch.object(
            CertificationSiiEnvelopeGateway,
            "send_envelope",
            side_effect=SiiTransportError(
                "Synthetic uncertain upload", dispatched=True
            ),
        ) as send:
            submission._write_internal({"state": "pending"})
            submission._job_send()
            self.assertEqual(submission.state, "outcome_unknown")
            submission._job_send()
            send.assert_called_once()
