# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
from copy import deepcopy
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from lxml import etree

from odoo.exceptions import UserError, ValidationError
from odoo.tools.safe_eval import safe_eval

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_signing.tests.common import SigningTestCase

from ..services.certification_auth import CertificationAuthentication, SiiTransportError
from ..services.certification_response_parser import (
    parse_status,
    parse_upload,
    parse_xml,
)
from ..services.certification_transport import CertificationSiiTransport


def soap_response(operation, payload):
    root = etree.Element("{http://schemas.xmlsoap.org/soap/envelope/}Envelope")
    body = etree.SubElement(root, "{http://schemas.xmlsoap.org/soap/envelope/}Body")
    etree.SubElement(body, operation + "Return").text = payload
    return etree.tostring(root)


def status_response(track="123", accepted=2, rejected=0, observed=0, code="EPR"):
    payload = (
        f"<RESPUESTA><RESP_HDR><TRACKID>{track}</TRACKID>"
        f"<ESTADO>{code}</ESTADO></RESP_HDR><RESP_BODY><TIPO_DOCTO>33</TIPO_DOCTO>"
        f"<INFORMADOS>{accepted+rejected+observed}</INFORMADOS>"
        f"<ACEPTADOS>{accepted}</ACEPTADOS><RECHAZADOS>{rejected}</RECHAZADOS>"
        f"<REPAROS>{observed}</REPAROS></RESP_BODY></RESPUESTA>"
    )
    return soap_response("getEstUp", payload)


def upload_response(status="0", track="123"):
    return (
        "<RECEPCIONDTE><RUTSENDER>12345678-5</RUTSENDER>"
        "<RUTCOMPANY>12345678-5</RUTCOMPANY>"
        f"<STATUS>{status}</STATUS><TRACKID>{track}</TRACKID></RECEPCIONDTE>"
    ).encode()


class TestCertificationTransport(SigningTestCase):
    def test_epr_summary_exposes_verified_document_counters(self):
        submission = self.submission()
        submission._write_internal({"track_id": "123"})
        for accepted, rejected, observed in (
            (0, 2, 0),
            (1, 1, 0),
            (1, 0, 1),
            (2, 0, 0),
        ):
            with self.subTest(accepted=accepted, rejected=rejected, observed=observed):
                attempt = self.env[
                    "l10n_cl.sii.envelope.attempt"
                ]._create_for_operation(submission, "status")
                attempt._complete(
                    "completed",
                    status_response(
                        accepted=accepted, rejected=rejected, observed=observed
                    ),
                )
                self.assertEqual(submission.reported_rejection, bool(rejected))
                self.assertIn("2 DTE", submission.response_notice)
                self.assertIn(f"{rejected} rejected", submission.response_notice)
                table = etree.HTML(submission.response_document_summary)
                self.assertEqual(
                    table.xpath("//tbody/tr/td/text()"),
                    ["33", "2", str(accepted), str(rejected), str(observed)],
                )
                self.assertEqual(submission.state, "pending")

    def test_epr_summary_rejects_counters_for_a_different_envelope_or_track(self):
        submission = self.submission()
        submission._write_internal({"track_id": "123"})
        for content in (
            status_response(accepted=0, rejected=1),
            status_response(track="999", accepted=0, rejected=2),
        ):
            with self.subTest(content=content):
                attempt = self.env[
                    "l10n_cl.sii.envelope.attempt"
                ]._create_for_operation(submission, "status")
                attempt._complete("completed", content)
                self.assertFalse(submission.reported_rejection)
                self.assertFalse(submission.response_document_summary)
                self.assertTrue(submission.response_warning)
                self.assertEqual(submission.state, "pending")

    def configure(self):
        company = self.company.with_user(self.dte_manager)
        company.partner_id.write(
            {"country_id": self.env.ref("base.cl").id, "vat": "12345678-5"}
        )
        company.sudo().write(
            {"dte_sii_environment": "certification", "dte_sii_sender_vat": "12345678-5"}
        )
        self.create_signing_profile(environment="certification")
        return company

    def envelope(self):
        return (
            b'<?xml version="1.0" encoding="ISO-8859-1"?>\n'
            b'<EnvioDTE xmlns="http://www.sii.cl/SiiDte" '
            b'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            b'xsi:schemaLocation="http://www.sii.cl/SiiDte EnvioDTE_v10.xsd">\n'
            b"<SetDTE>"
            b"<Caratula>"
            b"<RutEmisor>12345678-5</RutEmisor>"
            b"<RutEnvia>12345678-5</RutEnvia>"
            b"<NroResol>0</NroResol>"
            b"</Caratula>"
            b"<DTE>"
            b"<Documento>"
            b"<Encabezado>"
            b"<IdDoc>"
            b"<TipoDTE>33</TipoDTE>"
            b"</IdDoc>"
            b"</Encabezado>"
            b"</Documento>"
            b"</DTE>"
            b"<DTE>"
            b"<Documento>"
            b"<Encabezado>"
            b"<IdDoc>"
            b"<TipoDTE>33</TipoDTE>"
            b"</IdDoc>"
            b"</Encabezado>"
            b"</Documento>"
            b"</DTE>"
            b"</SetDTE>"
            b"</EnvioDTE>"
        )

    def submission(self):
        self.configure()
        data = self.envelope()
        return (
            self.env["l10n_cl.sii.envelope.submission"]
            .with_user(self.dte_manager)
            ._create_from_workflow(
                {
                    "name": "test.xml",
                    "company_id": self.company.id,
                    "environment": "certification",
                    "request_xml": base64.b64encode(data),
                    "request_digest": hashlib.sha256(data).hexdigest(),
                    "idempotency_key": "test-certification-envelope",
                }
            )
        )

    def test_signed_seed_digest_and_signature(self):
        company = self.configure()
        content = SignatureService().sign_authentication_seed(
            company, "certification", "000123"
        )
        root = parse_xml(content)
        signature = root[-1]
        info = signature[0]
        unsigned = deepcopy(root)
        unsigned.remove(unsigned[-1])
        expected = base64.b64encode(
            hashlib.sha1(etree.tostring(unsigned, method="c14n")).digest()
        ).decode()
        self.assertEqual(
            info.xpath('.//*[local-name()="DigestValue"]/text()'), [expected]
        )
        self.caf_test_key.public_key().verify(
            base64.b64decode(signature[1].text),
            etree.tostring(etree.fromstring(etree.tostring(info)), method="c14n"),
            padding.PKCS1v15(),
            hashes.SHA1(),
        )
        with self.assertRaises(UserError):
            SignatureService().sign_authentication_seed(
                company.with_user(self.dte_user), "certification", "123"
            )

    def test_authentication_uses_real_seed_and_escaped_signed_xml(self):
        company = self.configure()
        seed = soap_response(
            "getSeed",
            "<RESPUESTA><ESTADO>00</ESTADO><SEMILLA>000123</SEMILLA></RESPUESTA>",
        )
        token = soap_response(
            "getToken",
            "<RESPUESTA><ESTADO>00</ESTADO><TOKEN>abc123</TOKEN></RESPUESTA>",
        )
        with patch.object(
            CertificationAuthentication, "post", side_effect=[seed, token]
        ) as post:
            auth = CertificationAuthentication()
            self.assertEqual(auth.token(company), "abc123")
            token_request = post.call_args_list[1].kwargs["data"]
            self.assertIn(b"<![CDATA[", token_request)
            self.assertIn(b"<Semilla>", token_request)
            auth.close()

    def test_http_redirect_is_not_followed_after_dispatch(self):
        session = MagicMock()
        session.post.return_value.__enter__.return_value.status_code = 302
        auth = CertificationAuthentication(session)
        with self.assertRaises(SiiTransportError) as error:
            auth.post("/cgi_dte/UPL/DTEUpload", upload=True, data=b"x")
        self.assertTrue(error.exception.dispatched)
        self.assertFalse(session.post.call_args.kwargs["allow_redirects"])
        self.assertTrue(session.post.call_args.kwargs["verify"])

    def test_upload_preserves_exact_envelope_and_receipt(self):
        company = self.configure()
        with (
            patch.object(CertificationAuthentication, "token", return_value="abc123"),
            patch.object(
                CertificationAuthentication, "post", return_value=upload_response()
            ) as post,
            patch.object(
                SignatureService, "verify_envelope_signature", return_value=True
            ),
        ):
            result = CertificationSiiTransport().send_envelope(
                company, "certification", self.envelope(), "test"
            )
            self.assertEqual(result["track_id"], "123")
            self.assertEqual(
                post.call_args.kwargs["files"]["archivo"][1], self.envelope()
            )
            self.assertEqual(result["raw_response"], upload_response())
            self.assertEqual(post.call_args.kwargs["headers"]["Cookie"], "TOKEN=abc123")

    def test_bad_declaration_is_blocked_before_authentication(self):
        company = self.configure()
        old = self.envelope().replace(
            b'encoding="ISO-8859-1"', b"encoding='ISO-8859-1'"
        )
        with patch.object(CertificationAuthentication, "token") as token:
            with self.assertRaises(ValidationError):
                CertificationSiiTransport().send_envelope(
                    company, "certification", old, "test"
                )
            token.assert_not_called()

    def test_production_resolution_is_blocked_before_authentication(self):
        company = self.configure()
        wrong = self.envelope().replace(
            b"<NroResol>0</NroResol>", b"<NroResol>80</NroResol>"
        )
        with patch.object(CertificationAuthentication, "token") as token:
            with self.assertRaisesRegex(ValidationError, "resolution number 0"):
                CertificationSiiTransport().send_envelope(
                    company, "certification", wrong, "test"
                )
            token.assert_not_called()

    def test_sender_must_be_certificate_holder_before_authentication(self):
        company = self.configure()
        company.sudo().dte_sii_sender_vat = "76086428-5"
        with patch.object(CertificationAuthentication, "soap") as soap:
            with self.assertRaisesRegex(UserError, "certificate holder"):
                CertificationSiiTransport().test_authentication(
                    company, "certification"
                )
            soap.assert_not_called()

    def test_receipt_rut_leading_zero_is_not_a_different_sender(self):
        response = upload_response().replace(b"12345678-5", b"012345678-5")
        result = parse_upload(response, "12.345.678-5", "12345678-5")
        self.assertEqual(result["track_id"], "123")

    def test_invalid_upload_is_not_queued(self):
        submission = self.submission()
        bad = self.envelope().replace(
            b'encoding="ISO-8859-1"', b"encoding='ISO-8859-1'"
        )
        submission._write_internal(
            {
                "request_xml": base64.b64encode(bad),
                "request_digest": hashlib.sha256(bad).hexdigest(),
            }
        )
        with patch.object(type(submission), "with_delay") as queue:
            with self.assertRaises(ValidationError):
                submission.action_enqueue_send()
            queue.assert_not_called()

    def test_upload_rejection_does_not_invent_track_id(self):
        result = parse_upload(upload_response("7", ""), "12345678-5", "12345678-5")
        self.assertEqual(result["state"], "rejected")
        self.assertFalse(result["track_id"])

    def test_receipt_identity_mismatch_is_rejected(self):
        with self.assertRaises(ValidationError):
            parse_upload(upload_response(), "76086428-5", "12345678-5")

    def test_status_distinguishes_processing_rejection_and_observations(self):
        for code, accepted, rejected, observed, expected in [
            ("SOK", 0, 0, 0, "processing"),
            ("EPR", 2, 0, 0, "accepted"),
            ("EPR", 0, 2, 0, "rejected"),
            ("EPR", 1, 1, 0, "partially_accepted"),
            ("EPR", 1, 0, 1, "accepted_with_observations"),
            ("ZZZ", 2, 0, 0, "outcome_unknown"),
        ]:
            with self.subTest(code=code, expected=expected):
                self.assertEqual(
                    parse_status(
                        status_response(
                            code=code,
                            accepted=accepted,
                            rejected=rejected,
                            observed=observed,
                        ),
                        "123",
                    )["state"],
                    expected,
                )

    def test_processed_without_counters_is_not_accepted(self):
        xml = soap_response(
            "getEstUp",
            "<RESPUESTA><TRACKID>123</TRACKID><ESTADO>EPR</ESTADO></RESPUESTA>",
        )
        self.assertEqual(parse_status(xml, "123")["state"], "outcome_unknown")

    def test_status_track_id_and_xml_entities_are_rejected(self):
        with self.assertRaises(ValidationError):
            parse_status(status_response(track="124"), "123")
        with self.assertRaises(ValidationError):
            parse_xml(
                b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'
            )

    def test_factory_and_configuration_never_fall_back_to_demo(self):
        gateway = self.env["l10n_cl.sii.gateway.factory"].get_envelope_gateway(
            "certification"
        )
        self.assertIsInstance(gateway, CertificationSiiTransport)
        with self.assertRaises(UserError):
            gateway.validate_configuration(self.company, "certification")
        with self.assertRaises(UserError):
            self.env["l10n_cl.sii.gateway.factory"].get_envelope_gateway("production")

    def test_authentication_error_uses_operator_language_without_http_request(self):
        company = self.configure().with_context(lang="es_CL")
        auth = CertificationAuthentication()
        # The shared fixture forces English; exercise Odoo's real language lookup.
        self._translation_patcher.stop()
        try:
            with patch.object(
                auth,
                "soap",
                side_effect=[
                    soap_response(
                        "getSeed", "<R><ESTADO>00</ESTADO><SEMILLA>123</SEMILLA></R>"
                    ),
                    soap_response("getToken", "<R><ESTADO>11</ESTADO></R>"),
                ],
            ):
                with self.assertRaisesRegex(UserError, "El SII rechazó"):
                    auth.token(company)
        finally:
            self._translation_patcher.start()
            auth.close()

    def test_authentication_proof_is_required_and_bound_to_identity(self):
        company = self.configure()
        transport = CertificationSiiTransport()
        with self.assertRaises(UserError):
            transport.validate_authentication_state(company, "certification")
        profile = company.env["l10n_cl.dte.signing.profile"].search(
            [("company_id", "=", company.id), ("sii_environment", "=", "certification")]
        )
        profile._record_sii_authentication(True)
        self.assertTrue(
            transport.validate_authentication_state(company, "certification")
        )
        with self.assertRaises(UserError):
            profile.write({"sii_auth_verified_until": False})
        with self.assertRaises(UserError):
            profile.create({"sii_auth_verified_until": False})
        company.sudo().dte_sii_sender_vat = "76086428-5"
        with self.assertRaises(UserError):
            transport.validate_authentication_state(company, "certification")

    def test_failed_authentication_clears_previous_proof(self):
        company = self.configure()
        profile = company.env["l10n_cl.dte.signing.profile"].search(
            [("company_id", "=", company.id), ("sii_environment", "=", "certification")]
        )
        profile._record_sii_authentication(True)
        caught = None
        with (
            patch.object(
                CertificationAuthentication,
                "token",
                side_effect=UserError("Authentication denied"),
            ),
        ):
            try:
                CertificationSiiTransport().test_authentication(
                    company, "certification"
                )
            except UserError as error:
                caught = error
            else:
                self.fail("Authentication should have failed")
        self.assertIsInstance(caught, UserError)
        self.assertFalse(profile.sii_auth_verified_until)

    @staticmethod
    def claim_in_test_transaction(submission):
        # TransactionCase rows are intentionally invisible to independent cursors.
        # The actual committed claim is exercised by the isolated DB acceptance run.
        attempt = submission.env["l10n_cl.sii.envelope.attempt"]._create_for_operation(
            submission, "send"
        )
        submission._write_internal({"state": "sending"})
        return attempt.id

    @staticmethod
    def dispatch_in_test_transaction(submission, attempt_id):
        return submission._send_claimed(attempt_id)

    def test_recorded_track_id_and_duplicate_job_does_not_resend(self):
        submission = self.submission()
        gateway = MagicMock()
        gateway.send_envelope.return_value = {
            "state": "submitted",
            "track_id": "123",
            "raw_response": upload_response(),
        }
        with (
            patch.object(
                type(self.env["l10n_cl.sii.gateway.factory"]),
                "get_envelope_gateway",
                return_value=gateway,
            ),
            patch.object(
                type(submission), "_claim_dispatch", self.claim_in_test_transaction
            ),
            patch.object(
                type(submission), "_dispatch_claimed", self.dispatch_in_test_transaction
            ),
        ):
            submission._job_send()
            submission._job_send()
        self.assertEqual(submission.track_id, "123")
        self.assertEqual(submission.state, "submitted")
        gateway.send_envelope.assert_called_once()
        self.assertEqual(
            submission.attempt_ids.response_digest,
            hashlib.sha256(upload_response()).hexdigest(),
        )

    def test_post_dispatch_timeout_blocks_resending(self):
        submission = self.submission()
        gateway = MagicMock()
        gateway.send_envelope.side_effect = SiiTransportError(
            "connection failed", dispatched=True
        )
        with (
            patch.object(
                type(self.env["l10n_cl.sii.gateway.factory"]),
                "get_envelope_gateway",
                return_value=gateway,
            ),
            patch.object(
                type(submission), "_claim_dispatch", self.claim_in_test_transaction
            ),
        ):
            with patch.object(
                type(submission), "_dispatch_claimed", self.dispatch_in_test_transaction
            ):
                submission._job_send()
                submission._job_send()
        self.assertEqual(submission.state, "outcome_unknown")
        gateway.send_envelope.assert_called_once()

    def test_incomplete_status_preserves_raw_evidence_without_acceptance(self):
        submission = self.submission()
        submission._write_internal({"state": "submitted", "track_id": "123"})
        raw = status_response(accepted=1)
        gateway = MagicMock()
        gateway.query_envelope.return_value = parse_status(raw, "123")
        with patch.object(
            type(self.env["l10n_cl.sii.gateway.factory"]),
            "get_envelope_gateway",
            return_value=gateway,
        ):
            submission._job_query()
        self.assertEqual(submission.state, "outcome_unknown")
        self.assertEqual(base64.b64decode(submission.attempt_ids.response_xml), raw)

    def test_refresh_opens_same_dialog_without_queueing_or_changing_evidence(self):
        submission = self.submission()
        before = (submission.state, submission.write_date, submission.request_digest)
        with (
            patch.object(type(submission), "with_delay") as queue,
            patch.object(CertificationAuthentication, "post") as network,
        ):
            action = submission.action_refresh_response()
            queue.assert_not_called()
            network.assert_not_called()
        self.assertEqual(action["res_id"], submission.id)
        self.assertEqual(action["target"], "new")
        self.assertEqual(action["res_model"], submission._name)
        self.assertEqual(
            (submission.state, submission.write_date, submission.request_digest), before
        )

    def test_dialog_always_offers_refresh_and_queries_only_with_track(self):
        submission = self.submission()
        view = submission.get_view(
            view_id=self.env.ref(
                "l10n_cl_dte_gateway.view_sii_envelope_submission_form"
            ).id,
            view_type="form",
        )
        arch = etree.fromstring(view["arch"])
        for state, track, query in [
            ("sending", False, False),
            ("submitted", "123", True),
            ("submitted", False, False),
            ("processing", "123", True),
            ("outcome_unknown", "123", True),
            ("outcome_unknown", False, False),
            ("accepted", "123", False),
            ("rejected", False, False),
        ]:
            with self.subTest(state=state, track=track):
                visible = {
                    button.get("name")
                    for button in arch.xpath("./header/button")
                    if not safe_eval(
                        button.get("invisible", "False"),
                        {"state": state, "track_id": track},
                    )
                }
                self.assertIn("action_refresh_response", visible)
                self.assertEqual("action_enqueue_status_query" in visible, query)

    def test_status_query_preserves_dialog_and_does_not_resend(self):
        submission = self.submission()
        submission._write_internal({"state": "submitted", "track_id": "123"})
        with patch.object(type(submission), "with_delay") as queue:
            action = submission.action_enqueue_status_query()
            queue.return_value._job_query.assert_called_once_with()
            queue.return_value._job_send.assert_not_called()
        self.assertEqual(action["target"], "new")
        self.assertEqual(action["res_id"], submission.id)

    def test_refresh_rejects_non_manager_and_status_query_requires_track(self):
        submission = self.submission()
        with self.assertRaises(UserError):
            submission.with_user(self.dte_user).action_refresh_response()
        submission._write_internal({"state": "submitted"})
        with patch.object(type(submission), "with_delay") as queue:
            with self.assertRaises(UserError):
                submission.action_enqueue_status_query()
            queue.assert_not_called()

    def test_non_manager_cannot_send(self):
        submission = self.submission()
        with self.assertRaises(UserError):
            submission.with_user(self.dte_user).action_enqueue_send()
