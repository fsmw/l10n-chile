import base64
import hashlib
from copy import deepcopy
from unittest.mock import MagicMock, patch

import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from lxml import etree

from odoo.exceptions import AccessError, ValidationError

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_signing.tests.common import SigningTestCase

from ..services.certification_auth import CertificationAuthentication, SiiTransportError
from ..services.certification_response_parser import parse_xml, soap_payload


def response(operation, content):
    envelope = etree.Element("{http://schemas.xmlsoap.org/soap/envelope/}Envelope")
    etree.SubElement(envelope, operation + "Return").text = content
    return etree.tostring(envelope)


class TestCertificationAuthentication(SigningTestCase):
    def session(self, *contents):
        session = MagicMock()
        replies = []
        for content in contents:
            reply = MagicMock()
            reply.__enter__.return_value.status_code = 200
            reply.__enter__.return_value.iter_content.return_value = [content]
            replies.append(reply)
        session.post.side_effect = replies
        return session

    def test_seed_token_roundtrip_verifies_signature_and_closes_session(self):
        self.create_signing_profile(environment="certification")
        session = self.session(
            response("getSeed", "<R><ESTADO>00</ESTADO><SEMILLA>004200</SEMILLA></R>"),
            response(
                "getToken", "<R><ESTADO>00</ESTADO><TOKEN>synthetic42</TOKEN></R>"
            ),
        )
        auth = CertificationAuthentication(session)
        company = self.company.with_user(self.dte_manager)
        self.assertEqual(auth.token(company), "synthetic42")
        request = parse_xml(session.post.call_args.kwargs["data"])
        signed = parse_xml(
            request.xpath(".//*[local-name()='pszXml']/text()")[0].encode()
        )
        self.assertEqual(signed.findtext("item/Semilla"), "004200")
        signature = signed[-1]
        unsigned = deepcopy(signed)
        unsigned.remove(unsigned[-1])
        expected = base64.b64encode(
            hashlib.sha1(etree.tostring(unsigned, method="c14n")).digest()
        ).decode()
        self.assertEqual(
            signature[0].xpath('.//*[local-name()="DigestValue"]/text()'), [expected]
        )
        info = etree.fromstring(etree.tostring(signature[0]))
        self.caf_test_key.public_key().verify(
            base64.b64decode(signature[1].text),
            etree.tostring(info, method="c14n"),
            padding.PKCS1v15(),
            hashes.SHA1(),
        )
        for call in session.post.call_args_list:
            self.assertTrue(call.args[0].startswith("https://maullin.sii.cl/DTEWS/"))
            self.assertFalse(call.kwargs["allow_redirects"])
            self.assertTrue(call.kwargs["verify"])
        self.assertFalse(session.trust_env)
        auth.close()
        session.cookies.clear.assert_called_once()
        session.close.assert_called_once()

    def test_seed_signing_rejects_other_environments_and_operators(self):
        self.create_signing_profile(environment="certification")
        for environment, seed in [("production", "42"), ("certification", "invalid")]:
            with (
                self.subTest(environment=environment),
                self.assertRaises(ValidationError),
            ):
                SignatureService().sign_authentication_seed(
                    self.company, environment, seed
                )
        with self.assertRaises(AccessError):
            SignatureService().sign_authentication_seed(
                self.company.with_user(self.dte_user), "certification", "42"
            )

    def test_invalid_certificate_identity_fails_before_requesting_seed(self):
        self.create_signing_profile(environment="certification")
        session = self.session()
        auth = CertificationAuthentication(session)
        with (
            patch.object(
                SignatureService,
                "certification_signing_identity",
                side_effect=ValidationError("Synthetic invalid identity"),
            ),
            self.assertRaises(ValidationError),
        ):
            auth.token(self.company.with_user(self.dte_manager))
        session.post.assert_not_called()
        auth.close()

    def test_rejected_or_invalid_tokens_are_not_returned(self):
        self.create_signing_profile(environment="certification")
        seed = response("getSeed", "<R><ESTADO>00</ESTADO><SEMILLA>42</SEMILLA></R>")
        for status, token in [("01", "ignored"), ("00", "invalid!"), ("00", "a" * 65)]:
            payload = f"<R><ESTADO>{status}</ESTADO><TOKEN>{token}</TOKEN></R>"
            auth = CertificationAuthentication(
                self.session(seed, response("getToken", payload))
            )
            with (
                self.subTest(status=status, token=token),
                self.assertRaises(SiiTransportError),
            ):
                auth.token(self.company.with_user(self.dte_manager))
            auth.close()

    def test_untrusted_xml_and_ambiguous_soap_are_rejected(self):
        for content in [
            b"",
            b"x" * 2_000_001,
            b"<R>",
            b'<!DOCTYPE R [<!ENTITY x "test">]><R>&x;</R>',
        ]:
            with self.subTest(size=len(content)), self.assertRaises(ValidationError):
                parse_xml(content)
        duplicate = b'<Envelope xmlns="http://schemas.xmlsoap.org/soap/envelope/"><getSeedReturn>x</getSeedReturn><getSeedReturn>y</getSeedReturn></Envelope>'
        for content in [b"<R/>", duplicate, response("Fault", "synthetic fault")]:
            with self.subTest(content=content), self.assertRaises(ValidationError):
                soap_payload(content, "getSeed")

    def test_http_failure_and_oversize_response_fail_safely(self):
        session = self.session(b"x" * 2_000_001)
        auth = CertificationAuthentication(session)
        with self.assertRaises(SiiTransportError):
            auth.post("/DTEWS/CrSeed.jws")
        session = MagicMock()
        session.post.return_value.__enter__.return_value.status_code = 302
        auth = CertificationAuthentication(session)
        with self.assertRaises(SiiTransportError):
            auth.post("/DTEWS/CrSeed.jws")
        session.post.side_effect = requests.ConnectionError(
            "synthetic confidential detail"
        )
        with self.assertRaises(SiiTransportError) as error:
            auth.post("/DTEWS/CrSeed.jws")
        self.assertNotIn("confidential", str(error.exception))
        auth.close()
