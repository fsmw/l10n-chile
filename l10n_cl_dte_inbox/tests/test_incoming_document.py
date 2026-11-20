# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
from unittest.mock import patch

from odoo.addons.l10n_cl_dte_gateway.services.certification_auth import (
    SiiTransportError,
)
from odoo.addons.l10n_cl_dte_gateway.services.claim_registry import (
    SiiClaimRegistryClient,
)
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase


class TestIncomingDte(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.manager = cls.env.ref("l10n_cl_dte_core.group_dte_manager")
        cls.env.user.sudo().write({"groups_id": [(4, cls.manager.id)]})
        cls.company.partner_id.vat = "66666666-6"

    def test_import_stores_exact_digest_and_invalid_unsigned_xml(self):
        content = self._xml()
        record = self.env["l10n_cl.incoming.dte"]._import_xml(content)
        self.assertEqual(record.xml_sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual(record.xml_validation_state, "invalid")
        self.assertEqual(record.signature_state, "invalid")
        self.assertEqual(record.signer_trust_state, "unverified")
        self.assertEqual(base64.b64decode(record.raw_xml), content)
        self.assertIn("imported", record.event_ids.mapped("event_code"))

    def test_import_is_idempotent_for_same_xml(self):
        model = self.env["l10n_cl.incoming.dte"]
        first = model._import_xml(self._xml())
        second = model._import_xml(self._xml())
        self.assertEqual(first, second)

    def test_import_rejects_wrong_receiver(self):
        xml = self._xml().replace(b"66666666-6", b"76000000-0")
        with self.assertRaises(ValidationError):
            self.env["l10n_cl.incoming.dte"]._import_xml(xml)

    def test_direct_mutation_and_deletion_are_blocked(self):
        record = self.env["l10n_cl.incoming.dte"]._import_xml(self._xml())
        with self.assertRaises(UserError):
            record.write({"state": "reviewed"})
        with self.assertRaises(UserError):
            record.unlink()

    def test_review_adds_audit_event(self):
        record = self.env["l10n_cl.incoming.dte"]._import_xml(self._xml())
        record.action_mark_reviewed()
        self.assertEqual(record.state, "reviewed")
        self.assertIn("reviewed", record.event_ids.mapped("event_code"))

    def _response(self, code="ACD", environment=None):
        document = self.env["l10n_cl.incoming.dte"]._import_xml(self._xml())
        document.action_mark_reviewed()
        if environment:
            self.env.cr.execute(
                "UPDATE l10n_cl_incoming_dte SET sii_environment = %s WHERE id = %s",
                [environment, document.id],
            )
            document.invalidate_recordset()
        response = self.env["l10n_cl.incoming.dte.response"]._create_from_document(
            document, code, "Reviewed", "SII registroreclamodteservice"
        )
        return document, response.with_context(queue_job__no_delay=True)

    def test_response_is_durable_idempotent_and_only_sii_actions(self):
        document, response = self._response()
        duplicate = self.env["l10n_cl.incoming.dte.response"]._create_from_document(
            document, "ACD", "Reviewed", "SII registroreclamodteservice"
        )
        self.assertEqual(response, duplicate)
        self.assertEqual(response.state, "pending")
        self.assertIn("response_prepared", document.event_ids.mapped("event_code"))
        with self.assertRaises(UserError):
            self.env["l10n_cl.incoming.dte.response"]._create_from_document(
                document, "OPERATOR-CONFIRMED", "Reviewed", "contract"
            )

    def test_demo_response_is_simulated(self):
        document, response = self._response()
        response.action_enqueue()
        self.assertEqual(response.state, "accepted")
        self.assertEqual(response.external_reference, "simulated")
        self.assertIn("response_accepted", document.event_ids.mapped("event_code"))

    def _registry(self, method, **kwargs):
        return patch.object(SiiClaimRegistryClient, method, **kwargs)

    def test_production_claim_is_registered_or_rejected(self):
        for code, success, state in ((0, True, "accepted"), (9, False, "rejected")):
            with self.subTest(code=code), self.cr.savepoint() as savepoint:
                _document, response = self._response("RCD", "production")
                answer = {
                    "code": code,
                    "description": "Respuesta SII",
                    "events": [],
                    "success": success,
                    "raw_response": b"<answer/>",
                }
                with self._registry("register_action", return_value=answer) as call:
                    response.action_enqueue()
                self.assertEqual(
                    call.call_args.args[1:], ("76000000-0", "33", 12, "RCD")
                )
                self.assertEqual(response.state, state)
                self.assertEqual(response.external_reference, str(code))
                self.assertEqual(response.attempt_ids.transport_code, str(code))
                savepoint.rollback()

    def test_lost_answer_is_confirmed_from_events(self):
        _document, response = self._response("ACD", "production")
        lost = SiiTransportError("Timeout", dispatched=True)
        with self._registry("register_action", side_effect=lost):
            response.action_enqueue()
        self.assertEqual(response.state, "outcome_unknown")
        events = {
            "code": 15,
            "description": "Eventos",
            "events": [
                {"code": "ACD", "description": "", "responsible": "", "date": ""}
            ],
            "success": False,
            "raw_response": b"<events/>",
        }
        with self._registry("list_events", return_value=events):
            response.action_poll()
        self.assertEqual(response.state, "accepted")
        self.assertEqual(len(response.attempt_ids), 2)

    @classmethod
    def _xml(cls):
        receiver = cls.company.partner_id.vat.encode()
        return (
            b"""<DTE xmlns="http://www.sii.cl/SiiDte"><Documento ID="D">
        <Encabezado><IdDoc><TipoDTE>33</TipoDTE><Folio>12</Folio><FchEmis>2026-10-15</FchEmis></IdDoc>
        <Emisor><RUTEmisor>76000000-0</RUTEmisor></Emisor><Receptor><RUTRecep>"""
            + receiver
            + b"""</RUTRecep></Receptor>
        </Encabezado></Documento></DTE>"""
        )
