# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib

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

    def test_response_transmission_fails_closed(self):
        with self.assertRaises(UserError):
            self.env["l10n_cl.incoming.dte.response"].browse().action_enqueue()

    def test_response_is_durable_idempotent_and_not_transmitted(self):
        document = self.env["l10n_cl.incoming.dte"]._import_xml(self._xml())
        document.action_mark_reviewed()
        response_model = self.env["l10n_cl.incoming.dte.response"]
        response = response_model._create_from_document(
            document, "OPERATOR-CONFIRMED", "Reviewed", "SII contract reference"
        )
        duplicate = response_model._create_from_document(
            document, "OPERATOR-CONFIRMED", "Reviewed", "SII contract reference"
        )
        self.assertEqual(response, duplicate)
        self.assertEqual(response.state, "pending")
        self.assertIn("response_prepared", document.event_ids.mapped("event_code"))
        with self.assertRaises(UserError):
            response.action_enqueue()

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
