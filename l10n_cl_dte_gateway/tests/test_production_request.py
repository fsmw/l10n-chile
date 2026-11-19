# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from unittest.mock import patch

from lxml import etree

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import (
    DteXmlComposer,
    _extract_caf_bytes,
    _extract_dd_bytes,
)

from ..services.envelope_composer import SiiEnvelopeComposer
from ..services.production_gateway import ProductionSiiGateway
from ..services.production_transport import ProductionSiiTransport
from .common_production_request import ProductionRequestCase


@tagged("post_install", "-at_install")
class TestProductionRequest(ProductionRequestCase):
    def test_prepare_once_preserves_exact_bytes_without_network(self):
        with patch.object(ProductionSiiTransport, "send_envelope") as send:
            artifact = self.submission._prepare_production_request()
            raw = self.submission._verify_production_request()
            with patch.object(
                ProductionSiiGateway,
                "prepare_document",
                side_effect=AssertionError("No recomposition"),
            ):
                self.assertEqual(
                    self.submission._prepare_production_request(), artifact
                )
            send.assert_not_called()
        self.assertEqual(base64.b64decode(artifact.content), raw)
        self.assertEqual(base64.b64decode(self.original.content), self.signed)
        self.assertFalse(self.submission.attempt_ids)
        self.assertEqual(self.submission.state, "pending")

    def test_valid_alternative_and_multiple_dtes_are_rejected_atomically(self):
        altered = self.unsigned.replace(b"<DscItem>", b"<DscItem>Alternative ")
        signed = SignatureService().sign_document(self.company, "production", altered)
        alternative = ProductionSiiGateway().prepare_document(
            self.company, "production", signed
        )
        root = etree.fromstring(self.unsigned)
        root[0].set("ID", root[0].get("ID") + "-second")
        second = SignatureService().sign_document(
            self.company, "production", etree.tostring(root)
        )
        multiple = SiiEnvelopeComposer().compose(
            self.company,
            [self.signed, second],
            {
                "id": "SetDTE-multiple",
                "sender_vat": self.company.dte_sii_sender_vat,
                "resolution_number": self.company.dte_sii_resolution_number,
                "resolution_date": self.company.dte_sii_resolution_date,
            },
            environment="production",
        )
        before = self.document.artifact_ids
        for raw in (alternative, multiple):
            ProductionSiiTransport().validate_request(self.company, "production", raw)
            with patch.object(
                ProductionSiiGateway, "prepare_document", return_value=raw
            ):
                with self.assertRaisesRegex(ValidationError, "exact signed DTE"):
                    self.submission._prepare_production_request()
            self.assertFalse(self.submission.transport_request_artifact_id)
            self.assertEqual(self.document.artifact_ids, before)

    def test_reindented_caf_in_the_ted_is_the_reserved_caf(self):
        # Invoices and notes use the reference wire layout, which re-indents
        # the CAF inside the TED.
        ted = DteXmlComposer().compose_ted(self.snapshot, self.caf, invoice_layout=True)
        self.assertNotIn(_extract_caf_bytes(base64.b64decode(self.caf.caf_file)), ted)
        unsigned = DteXmlComposer().compose_unsigned_document(
            {**self.snapshot, "ted_xml": ted}
        )
        signed = SignatureService().sign_document(self.company, "production", unsigned)
        previous = self.document._get_signed_artifact()
        replacement = self.create_artifact(self.document, "signed_xml", signed)
        self.cr.execute(
            "UPDATE l10n_cl_electronic_artifact SET supersedes_id = %s WHERE id = %s",
            [previous.id, replacement.id],
        )
        replacement.invalidate_recordset()
        self.assertTrue(self.submission._prepare_production_request())

    def test_signed_ted_must_match_reserved_caf_and_identity(self):
        data = self.build_caf_xml(
            issuer_vat=self.company.vat, key_id="987654322", folio_end=102
        )
        other = self.env["l10n_cl.caf.authorization"].create(
            {
                "name": "Different certification CAF",
                "company_id": self.company.id,
                "sii_environment": "certification",
                "is_synthetic": False,
                "document_code": "33",
                "issuer_vat": self.company.vat,
                "folio_start": 100,
                "folio_end": 102,
                "date_from": fields.Date.today(),
                "caf_file": base64.b64encode(data),
            }
        )
        other.action_validate_authorization()
        ted = DteXmlComposer().compose_ted(self.snapshot, other)
        ted = ted.replace(b"<CAF ", b'<\xf1:CAF xmlns:\xf1="http://www.sii.cl/SiiDte" ')
        dd = _extract_dd_bytes(ted.replace(b"</CAF>", b"</\xf1:CAF>"))
        frmt = SignatureService().sign_ted(self.company, other, dd).encode()
        ted = (
            b'<TED version="1.0">'
            + dd
            + b'<FRMT algoritmo="SHA1withRSA">'
            + frmt
            + b"</FRMT></TED>"
        )
        wrong = DteXmlComposer().compose_ted({**self.snapshot, "folio": 101}, self.caf)
        for embedded_ted, message in (
            (ted, "reserved Production CAF"),
            (wrong, "signed DTE identity"),
        ):
            unsigned = DteXmlComposer().compose_unsigned_document(
                {**self.snapshot, "ted_xml": embedded_ted}
            )
            signed = SignatureService().sign_document(
                self.company, "production", unsigned
            )
            previous = self.document._get_signed_artifact()
            replacement = self.create_artifact(self.document, "signed_xml", signed)
            self.cr.execute(
                "UPDATE l10n_cl_electronic_artifact "
                "SET supersedes_id = %s WHERE id = %s",
                [previous.id, replacement.id],
            )
            replacement.invalidate_recordset()
            before = self.document.artifact_ids
            with self.assertRaisesRegex(ValidationError, message):
                self.submission._prepare_production_request()
            self.assertFalse(self.submission.transport_request_artifact_id)
            self.assertEqual(self.document.artifact_ids, before)

    def test_verification_rechecks_integrity_and_company_mode(self):
        with self.assertRaises(ValidationError):
            self.submission._verify_production_request()
        artifact = self.submission._prepare_production_request()
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.cr.execute(
                "UPDATE l10n_cl_electronic_artifact SET sha256 = %s WHERE id = %s",
                ["bad", artifact.id],
            )
            artifact.invalidate_recordset()
            self.submission._verify_production_request()
        self.company.sudo().write({"dte_sii_environment": "demo"})
        with self.assertRaises(UserError):
            self.submission._verify_production_request()

    def test_late_preparation_and_unauthorized_company_are_rejected(self):
        for state in ("sending", "accepted", "outcome_unknown"):
            self.submission._write_internal({"state": state})
            with self.assertRaises(UserError):
                self.submission._prepare_production_request()
        other = self.env["res.company"].sudo().create({"name": "Disabled DTE company"})
        denied = self.submission.with_user(self.dte_manager).with_context(
            allowed_company_ids=[other.id]
        )
        with self.assertRaises(AccessError):
            denied._prepare_production_request()

    def test_source_access_precedes_elevation(self):
        source = self.create_document()
        submission = self.submission.with_user(self.dte_manager)
        for action in ("_prepare_production_request", "_verify_production_request"):
            with self.assertRaises(AccessError):
                getattr(submission, action)()  # No linked source: fail closed.

        def discover(record):
            return source.with_env(record.env)

        with patch.object(type(submission), "_production_source", discover):
            self.assertFalse(submission.env.su)
            artifact = submission._prepare_production_request()
            self.assertEqual(
                submission._verify_production_request(),
                base64.b64decode(artifact.content),
            )
            before = self.document.artifact_ids
            rules = self.env["ir.rule"].sudo()
            for operation in ("read", "write"):
                rule = rules.create(
                    {
                        "name": "Deny production source",
                        "model_id": self.env["ir.model"]._get_id(source._name),
                        "domain_force": repr([("id", "!=", source.id)]),
                        "perm_read": operation == "read",
                        "perm_write": operation == "write",
                        "perm_create": False,
                        "perm_unlink": False,
                    }
                )
                self.assertTrue(submission.has_access("read"))
                self.assertFalse(source.has_access(operation))
                for action in (
                    "_prepare_production_request",
                    "_verify_production_request",
                ):
                    with self.assertRaises(AccessError):
                        getattr(submission, action)()
                rule.unlink()
            self.assertEqual(self.document.artifact_ids, before)
            self.assertEqual(submission.transport_request_artifact_id, artifact)

    def test_void_folio_blocks_preparation_atomically(self):
        self.env["l10n_cl.caf.folio"].search(
            [("document_id", "=", self.document.id)]
        ).action_void("Cancelled")
        before = self.document.artifact_ids
        with self.assertRaises(ValidationError):
            self.submission._prepare_production_request()
        self.assertFalse(self.submission.transport_request_artifact_id)
        self.assertEqual(self.document.artifact_ids, before)

    def test_void_folio_blocks_retained_envelope(self):
        self.submission._prepare_production_request()
        self.env["l10n_cl.caf.folio"].search(
            [("document_id", "=", self.document.id)]
        ).action_void("Cancelled")
        with self.assertRaises(ValidationError):
            self.submission._verify_production_request()
