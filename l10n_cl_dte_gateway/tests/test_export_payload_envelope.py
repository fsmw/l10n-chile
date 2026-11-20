# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hashlib
from copy import deepcopy
from unittest.mock import Mock, patch

from lxml import etree

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_signing.tests.common import SigningTestCase

from ..services.document_validation import get_document_validator
from ..services.envelope_composer import SiiEnvelopeComposer
from ..services.envelope_result_parser import _expected_documents

SII = "{http://www.sii.cl/SiiDte}"


@tagged("post_install", "-at_install")
class TestExportPayloadEnvelope(SigningTestCase):
    def setUp(self):
        super().setUp()
        self.company = self.company.with_user(self.dte_manager)
        self.company.sudo().write({"dte_sii_environment": "certification"})
        self.company.partner_id.sudo().write({"vat": "12345678-5"})
        self.create_signing_profile(environment="certification")
        self.header = {
            "id": "Set-export",
            "sender_vat": "12345678-5",
            "resolution_number": "0",
            "resolution_date": "2026-01-01",
        }
        # These contract fixtures test routing and real XMLDSig, not fiscal XML.
        self.schema = patch(
            "odoo.addons.l10n_cl_dte_gateway.services.envelope_composer._schema",
            return_value=Mock(spec=etree.XMLSchema),
        )
        self.schema.start()
        self.addCleanup(self.schema.stop)
        self.validator = Mock()
        self.validator.validate_document.side_effect = lambda content, **kw: (
            SignatureService().verify_signature(
                content, sii_standalone=kw.get("strict_sii", False)
            )
        )
        self.resolver = Mock(return_value=self.validator)

    def signed(self, code="110"):
        name = "Exportaciones" if code in {"110", "111", "112"} else "Documento"
        content = (
            f'<DTE xmlns="http://www.sii.cl/SiiDte"><{name} ID="F100T{code}">'
            f"<Encabezado><IdDoc><TipoDTE>{code}</TipoDTE><Folio>100</Folio>"
            "<FchEmis>2026-01-01</FchEmis></IdDoc>"
            "<Emisor><RUTEmisor>12345678-5</RUTEmisor></Emisor>"
            "<Receptor><RUTRecep>55555555-5</RUTRecep></Receptor>"
            f"</Encabezado></{name}></DTE>"
        ).encode()
        return SignatureService().sign_document(self.company, "certification", content)

    def test_typed_batch_routes_and_preserves_signed_bytes_and_identities(self):
        codes = ["33", "110", "111", "112"]
        documents = [self.signed(code) for code in codes]
        header = deepcopy(self.header)
        composer = SiiEnvelopeComposer(document_validator_getter=self.resolver)
        envelope = composer.compose(self.company, documents, header)
        self.assertTrue(composer.validate(envelope, expected_count=4))
        self.assertEqual(
            [call.args[0] for call in self.resolver.call_args_list], codes * 3
        )
        for content in documents:
            self.assertIn(content.split(b"?>", 1)[-1].lstrip(b"\n"), envelope)
        for call in self.validator.validate_document.call_args_list:
            self.assertTrue(call.kwargs["strict_sii"])
        self.assertEqual(header, self.header)
        root = etree.fromstring(envelope)
        expected = _expected_documents(root)
        self.assertEqual([item["document_type"] for item in expected], codes)
        for row, dte in zip(
            expected, root.findall(f"{SII}SetDTE/{SII}DTE"), strict=True
        ):
            self.assertEqual(row["folio"], "100")
            self.assertEqual(row["receiver_vat"], "55555555-5")
            self.assertEqual(
                row["document_digest"],
                hashlib.sha256(
                    etree.tostring(dte, encoding="UTF-8", with_tail=False)
                ).hexdigest(),
            )

    def test_default_rejects_exports_before_envelope_signing(self):
        for code in ("110", "111", "112"):
            content = self.signed(code)
            with (
                self.subTest(code=code),
                patch.object(SignatureService, "sign_envelope") as sign,
            ):
                with self.assertRaisesRegex(ValidationError, "validator"):
                    SiiEnvelopeComposer().compose(self.company, [content], self.header)
                sign.assert_not_called()

    def test_invalid_payload_rejected_before_extension_dispatch(self):
        content = self.signed()
        variants = [
            content.replace(b"</DTE>", b"<Documento ID='other'/></DTE>"),
            content.replace(b"Exportaciones", b"Documento"),
            content.replace(b' ID="F100T110"', b""),
        ]
        composer = SiiEnvelopeComposer(document_validator_getter=self.resolver)
        for content in variants:
            with self.subTest(content=content), self.assertRaises(ValidationError):
                composer.compose(self.company, [content], self.header)
        self.resolver.assert_not_called()

    def test_batch_rejects_duplicate_and_set_payload_ids(self):
        content = self.signed()
        composer = SiiEnvelopeComposer(document_validator_getter=self.resolver)
        with self.assertRaises(ValidationError):
            composer.compose(self.company, [content, content], self.header)
        with self.assertRaises(ValidationError):
            composer.compose(self.company, [content], dict(self.header, id="F100T110"))

    def test_submission_dispatch_checks_record_code_before_hook(self):
        for code in ("33", "110", "111", "112"):
            document = self.create_document(document_code=code)
            submission = (
                self.env["l10n_cl.electronic.submission"]
                .with_user(self.dte_manager)
                .create(
                    {
                        "document_id": document.id,
                        "idempotency_key": f"export-test-{code}",
                    }
                )
            )
            content = self.signed(code)
            with patch.object(
                type(submission), "_get_document_validator", self.resolver
            ):
                self.assertTrue(submission._validate_document_payload(content))
                self.resolver.assert_called_with(code)
                self.validator.validate_document.assert_called_with(content)
                self.resolver.reset_mock()
                with self.assertRaises(ValidationError):
                    submission._validate_document_payload(self.signed("34"))
                self.resolver.assert_not_called()

    def test_default_validator_hook_is_closed_for_unknown_and_export_codes(self):
        # Export families need their own addon; the gateway default stays closed.
        for code in ("110", "111", "112", "999"):
            with (
                self.subTest(code=code),
                self.assertRaisesRegex(ValidationError, "validator"),
            ):
                get_document_validator(code)
        with self.assertRaisesRegex(ValidationError, "validator"):
            self.env["l10n_cl.electronic.submission"]._get_document_validator("999")

    def test_extension_validation_failure_stops_composition(self):
        content = self.signed()
        self.validator.validate_document.side_effect = ValidationError(
            "Invalid fiscal XML"
        )
        with patch.object(SignatureService, "sign_envelope") as sign:
            with self.assertRaisesRegex(ValidationError, "Invalid fiscal XML"):
                SiiEnvelopeComposer(document_validator_getter=self.resolver).compose(
                    self.company, [content], self.header
                )
            sign.assert_not_called()
