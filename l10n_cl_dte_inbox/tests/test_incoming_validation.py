# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
import json
from collections import Counter
from unittest.mock import patch

from lxml import etree
from odoo import Command
from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.services.ted_composer import DteTedComposer
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import DteXmlComposer
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import new_test_user
from psycopg2.errors import SerializationFailure, UniqueViolation

from ..services.incoming_parser import SII_NS
from ..services.incoming_validation import IncomingValidationService, xml_bytes


@tagged("post_install", "-at_install")
class TestIncomingValidation(DteInvoiceTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.receiver.sudo().vat = "76234567-6"
        cls.recipient = (
            cls.env["res.company"]
            .sudo()
            .create(
                {
                    "name": "Incoming synthetic recipient",
                    "partner_id": cls.receiver.id,
                    "currency_id": cls.env.ref("base.CLP").id,
                    "country_id": cls.chile.id,
                    "dte_sii_environment": "demo",
                }
            )
        )
        cls.dte_manager.sudo().write({"company_ids": [Command.link(cls.recipient.id)]})

    def setUp(self):
        super().setUp()
        self.create_signing_profile()
        self.imports = (
            self.env["l10n_cl.incoming.dte.import"]
            .with_company(self.recipient)
            .with_context(allowed_company_ids=[self.recipient.id])
        )

    def _signed(self, folio=710):
        snapshot, caf = self.capture(self.make_invoice(), folio=folio)
        composer = DteXmlComposer()
        snapshot["ted_xml"] = composer.compose_ted(snapshot, caf)
        return SignatureService().sign_document(
            self.company.with_user(self.dte_manager),
            "demo",
            composer.compose_unsigned_document(snapshot),
        )

    def _envelope(self, *documents):
        root = etree.Element(
            f"{{{SII_NS}}}EnvioDTE", nsmap={None: SII_NS}, version="1.0"
        )
        target = etree.SubElement(root, f"{{{SII_NS}}}SetDTE", ID="incoming-test-set")
        cover = etree.SubElement(target, f"{{{SII_NS}}}Caratula", version="1.0")
        for name, value in (
            ("RutEmisor", self.company.vat),
            ("RutEnvia", self.company.vat),
            ("RutReceptor", self.recipient.vat),
            ("FchResol", "2020-01-01"),
            ("NroResol", "80"),
            ("TmstFirmaEnv", "2026-10-15T12:00:00"),
        ):
            etree.SubElement(cover, f"{{{SII_NS}}}{name}").text = value
        for code, count in Counter(
            etree.fromstring(doc).findtext(
                f"{{{SII_NS}}}Documento/{{{SII_NS}}}Encabezado/{{{SII_NS}}}IdDoc/{{{SII_NS}}}TipoDTE"
            )
            for doc in documents
        ).items():
            subtotal = etree.SubElement(cover, f"{{{SII_NS}}}SubTotDTE")
            etree.SubElement(subtotal, f"{{{SII_NS}}}TpoDTE").text = code
            etree.SubElement(subtotal, f"{{{SII_NS}}}NroDTE").text = str(count)
        for doc in documents:
            target.append(etree.fromstring(doc))
        return SignatureService().sign_envelope(
            self.company.with_user(self.dte_manager), "demo", xml_bytes(root)
        )

    def _signed_boleta(self):
        snapshot, _caf = self.capture(self.make_invoice(), folio=740)
        root = etree.fromstring(DteXmlComposer().compose_unsigned_document(snapshot))
        document = root.find(f"{{{SII_NS}}}Documento")
        document.set("ID", "F740T39")
        header = document.find(f"{{{SII_NS}}}Encabezado")
        identification = header.find(f"{{{SII_NS}}}IdDoc")
        identification.find(f"{{{SII_NS}}}TipoDTE").text = "39"
        etree.SubElement(identification, f"{{{SII_NS}}}IndServicio").text = "3"
        etree.SubElement(identification, f"{{{SII_NS}}}IndMntNeto").text = "2"
        issuer = header.find(f"{{{SII_NS}}}Emisor")
        issuer.find(f"{{{SII_NS}}}RznSoc").tag = f"{{{SII_NS}}}RznSocEmisor"
        issuer.find(f"{{{SII_NS}}}GiroEmis").tag = f"{{{SII_NS}}}GiroEmisor"
        issuer.remove(issuer.find(f"{{{SII_NS}}}Acteco"))
        totals = header.find(f"{{{SII_NS}}}Totales")
        totals.remove(totals.find(f"{{{SII_NS}}}TasaIVA"))
        snapshot["document_code"] = "39"
        caf = self.validate_authorization(
            document_code="39", folio_start=740, folio_end=743
        )
        document.insert(
            len(document) - 1,
            etree.fromstring(DteTedComposer().compose_ted(snapshot, caf)),
        )
        return SignatureService().sign_document(
            self.company.with_user(self.dte_manager), "demo", xml_bytes(root)
        )

    def test_boleta_uses_its_own_schema_and_real_signatures(self):
        content = self._signed_boleta()
        batch = self.imports._import_xml(content)
        self.assertEqual(batch.state, "imported", batch.line_ids.diagnostics)
        self.assertEqual(batch.line_ids.document_code, "39")
        self.assertEqual(batch.line_ids.ted_state, "valid")

    def test_boleta_envelope_uses_boleta_schema(self):
        content = self._envelope(self._signed_boleta())
        root = etree.fromstring(content)
        # XMLDSig covers SetDTE; changing the unsigned root name retains its
        # namespace context and lets this fixture use the shared test signer.
        root.tag = f"{{{SII_NS}}}EnvioBOLETA"
        batch = self.imports._import_xml(xml_bytes(root))
        self.assertEqual(
            batch.state, "imported", batch.diagnostics + batch.line_ids.diagnostics
        )
        self.assertEqual(batch.signature_state, "valid")

    def test_invalid_ted_is_detected_under_a_valid_document_signature(self):
        root = etree.fromstring(self._signed())
        root.remove(root.find("{http://www.w3.org/2000/09/xmldsig#}Signature"))
        frmt = root.find(f"{{{SII_NS}}}Documento/{{{SII_NS}}}TED/{{{SII_NS}}}FRMT")
        frmt.text = base64.b64encode(bytes(128)).decode()
        content = SignatureService().sign_document(
            self.company.with_user(self.dte_manager), "demo", xml_bytes(root)
        )
        batch = self.imports._import_xml(content)
        self.assertEqual(batch.line_ids.signature_state, "valid")
        self.assertEqual(batch.line_ids.ted_state, "invalid")
        self.assertFalse(batch.line_ids.incoming_document_id)

    def test_signed_envelope_with_wrong_subtotals_is_rejected(self):
        root = etree.fromstring(self._envelope(self._signed()))
        root.remove(root.find("{http://www.w3.org/2000/09/xmldsig#}Signature"))
        root.find(
            f"{{{SII_NS}}}SetDTE/{{{SII_NS}}}Caratula/{{{SII_NS}}}SubTotDTE/{{{SII_NS}}}NroDTE"
        ).text = "2"
        content = SignatureService().sign_envelope(
            self.company.with_user(self.dte_manager), "demo", xml_bytes(root)
        )
        batch = self.imports._import_xml(content)
        self.assertEqual(batch.signature_state, "valid")
        self.assertEqual(batch.state, "invalid")
        self.assertIn("subtotals", batch.diagnostics)

    def test_unavailable_schema_is_explicitly_unsupported(self):
        content = self._signed()
        with patch(
            "odoo.addons.l10n_cl_dte_inbox.services.incoming_validation.file_path",
            side_effect=FileNotFoundError,
        ):
            result = IncomingValidationService(self.imports.env).inspect(content)
        self.assertEqual(result["documents"][0]["schema_state"], "unsupported")
        self.assertTrue(result["documents"][0]["diagnostics"])

    def test_signed_single_checks_real_schema_signature_ted_and_keeps_original(self):
        content = self._signed()
        batch = self.imports._import_xml(content)
        self.assertEqual(batch.state, "imported", batch.diagnostics)
        self.assertEqual(batch.imported_count, 1)
        document = batch.line_ids.incoming_document_id
        self.assertEqual(
            document.xml_validation_state, "validated", document.diagnostics
        )
        self.assertEqual(document.signer_trust_state, "unverified")
        self.assertEqual(len(document.signer_certificate_fingerprint), 64)
        self.assertEqual(base64.b64decode(document.raw_xml), content)
        self.assertEqual(base64.b64decode(batch.raw_xml), content)
        self.assertEqual(document.xml_sha256, hashlib.sha256(content).hexdigest())
        self.assertFalse(document.response_ids)

    def test_multi_document_envelope_imports_every_dte_and_keeps_signed_source(self):
        content = self._envelope(self._signed(), self._signed(720))
        batch = self.imports._import_xml(content)
        self.assertEqual(batch.state, "imported", batch.diagnostics)
        self.assertEqual(batch.imported_count, 2)
        self.assertEqual(batch.line_ids.mapped("signature_state"), ["valid", "valid"])
        self.assertEqual(base64.b64decode(batch.raw_xml), content)
        self.assertEqual(
            set(batch.line_ids.incoming_document_id.mapped("folio")), {710, 720}
        )

    def test_duplicate_file_is_idempotent_and_new_envelope_reuses_individual(self):
        content = self._signed()
        first = self.imports._import_xml(content)
        self.assertEqual(first, self.imports._import_xml(content))
        envelope = self.imports._import_xml(self._envelope(content))
        self.assertEqual(envelope.state, "imported", envelope.diagnostics)
        self.assertEqual(envelope.duplicate_count, 1)
        self.assertEqual(
            first.line_ids.incoming_document_id, envelope.line_ids.incoming_document_id
        )

    def test_one_tampered_document_blocks_entire_envelope(self):
        second = self._signed(720).replace(b"<MntTotal>", b"<MntTotal>9", 1)
        batch = self.imports._import_xml(self._envelope(self._signed(), second))
        self.assertEqual(batch.state, "invalid")
        self.assertFalse(batch.line_ids.incoming_document_id)
        self.assertEqual(batch.imported_count, 0)
        bad = batch.line_ids.filtered(lambda item: item.folio == 720)
        self.assertEqual(bad.signature_state, "invalid")
        self.assertIn(
            "signature", {item["stage"] for item in json.loads(bad.diagnostics)}
        )

    def test_tampered_outer_signature_blocks_valid_children(self):
        content = self._envelope(self._signed()).replace(
            b"<NroResol>80", b"<NroResol>81"
        )
        batch = self.imports._import_xml(content)
        self.assertEqual(batch.signature_state, "invalid")
        self.assertEqual(batch.line_ids.signature_state, "valid")
        self.assertFalse(batch.line_ids.incoming_document_id)

    def test_schema_error_has_line_and_stage(self):
        content = self._signed().replace(b"<NroLinDet>1</NroLinDet>", b"")
        batch = self.imports._import_xml(content)
        self.assertEqual(batch.state, "invalid")
        errors = json.loads(batch.line_ids.diagnostics)
        self.assertTrue(
            any(item["stage"] == "schema" and "line" in item for item in errors)
        )

    def test_wrong_company_is_diagnosed_without_importing(self):
        batch = self.env["l10n_cl.incoming.dte.import"]._import_xml(self._signed())
        self.assertEqual(batch.state, "invalid")
        self.assertIn("current company", batch.line_ids.diagnostics)

    def test_conflicting_identity_never_replaces_original(self):
        first = self.imports._import_xml(self._signed())
        original = first.line_ids.incoming_document_id
        digest = original.xml_sha256
        # Re-sign a changed document with the legitimate synthetic key.
        root = etree.fromstring(base64.b64decode(original.raw_xml))
        root.remove(root.find("{http://www.w3.org/2000/09/xmldsig#}Signature"))
        root.find(
            f"{{{SII_NS}}}Documento/{{{SII_NS}}}Detalle/{{{SII_NS}}}NmbItem"
        ).text = "Changed signed item"
        changed = SignatureService().sign_document(
            self.company.with_user(self.dte_manager), "demo", xml_bytes(root)
        )
        batch = self.imports._import_xml(changed)
        self.assertEqual(batch.state, "invalid")
        self.assertIn("conflict", batch.line_ids.diagnostics)
        self.assertEqual(original.xml_sha256, digest)

    def test_malformed_and_xxe_uploads_have_durable_diagnostics(self):
        for content in (
            b"<DTE>",
            b'<!DOCTYPE DTE [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><DTE>&xxe;</DTE>',
        ):
            with self.subTest(content=content):
                batch = self.imports._import_xml(content)
                self.assertEqual(batch.state, "invalid")
                self.assertFalse(batch.line_ids)
                self.assertIn("structure", batch.diagnostics)
                self.assertEqual(base64.b64decode(batch.raw_xml), content)

    def test_import_and_result_evidence_cannot_be_changed_or_deleted(self):
        batch = self.imports._import_xml(self._signed())
        for record in (batch, batch.line_ids):
            with self.assertRaises(UserError):
                record.write({"diagnostics": "replacement"})
            with self.assertRaises(UserError):
                record.unlink()

    def test_existing_environment_is_a_snapshot(self):
        batch = self.imports._import_xml(self._signed())
        document = batch.line_ids.incoming_document_id
        self.recipient.sudo().dte_sii_environment = "certification"
        self.assertEqual(batch.environment, "demo")
        self.assertEqual(document.sii_environment, "demo")

    def test_user_cannot_import_validate_or_read_raw_files(self):
        user = new_test_user(
            self.env(su=True),
            login="inbox_reader",
            groups="l10n_cl_dte_core.group_dte_user",
            company_id=self.recipient.id,
            company_ids=[Command.set(self.recipient.ids)],
        )
        content = self._signed()
        batch = self.imports._import_xml(content)
        with self.assertRaises(AccessError):
            self.imports.with_user(user)._import_xml(content)
        with self.assertRaises(AccessError):
            batch.line_ids.incoming_document_id.with_user(user).action_validate()
        with self.assertRaises(AccessError):
            batch.with_user(user).read(["raw_xml"])

    def test_other_company_cannot_read_import_or_lines(self):
        batch = self.imports._import_xml(self._signed())
        isolated = self.env(
            context={**self.env.context, "allowed_company_ids": [self.company.id]}
        )
        for record in (batch, batch.line_ids):
            with self.assertRaises(AccessError):
                record.with_env(isolated).read(["id"])

    def test_concurrent_identity_race_requests_full_transaction_retry(self):
        with (
            patch.object(
                type(self.imports),
                "_store_import",
                side_effect=UniqueViolation("concurrent insert"),
            ),
            self.assertRaises(SerializationFailure),
        ):
            self.imports._import_xml(self._signed())

    def test_local_revalidation_checks_source_integrity(self):
        document = self.imports._import_xml(
            self._signed()
        ).line_ids.incoming_document_id
        document.action_validate()
        self.assertEqual(document.xml_validation_state, "validated")
        self.assertIn("local_validation_valid", document.event_ids.mapped("event_code"))

    def test_extra_signature_reference_is_rejected(self):
        content = self._signed()
        root = etree.fromstring(content)
        ns = "http://www.w3.org/2000/09/xmldsig#"
        signed_info = root.find(f"{{{ns}}}Signature/{{{ns}}}SignedInfo")
        etree.SubElement(
            signed_info, f"{{{ns}}}Reference", URI="https://example.invalid/external"
        )
        batch = self.imports._import_xml(xml_bytes(root))
        self.assertEqual(batch.state, "invalid")
        self.assertIn("One local XML signature reference", batch.line_ids.diagnostics)
        self.assertEqual(batch.line_ids.signature_state, "invalid")

    def test_legal_base64_whitespace_in_xml_signature_values_is_supported(self):
        ns = "http://www.w3.org/2000/09/xmldsig#"
        for content in (self._signed(), self._envelope(self._signed(720))):
            with self.subTest(envelope=b"EnvioDTE" in content):
                root = etree.fromstring(content)
                value = root.find(f"{{{ns}}}Signature/{{{ns}}}SignatureValue")
                value.text = (
                    "\n"
                    + "\n".join(
                        value.text[i : i + 64] for i in range(0, len(value.text), 64)
                    )
                    + "\n"
                )
                batch = self.imports._import_xml(xml_bytes(root))
                self.assertEqual(
                    batch.state,
                    "imported",
                    batch.diagnostics + batch.line_ids.diagnostics,
                )
