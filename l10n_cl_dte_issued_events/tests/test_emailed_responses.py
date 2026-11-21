# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import datetime, timedelta
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

from freezegun import freeze_time

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import new_test_user

from .common import RECEPTION, ReceiverEventsCase


@tagged("post_install", "-at_install")
class TestEmailedResponses(ReceiverEventsCase):
    """RespuestaDTE and EnvioRecibos emailed back for our issued DTEs."""

    def setUp(self):
        super().setUp()
        # Answers arrive inside the legal window of the synthetic DTEs.
        clock = freeze_time(datetime.fromisoformat(RECEPTION) + timedelta(days=2))
        clock.start()
        self.addCleanup(clock.stop)
        self.responses = self.env["l10n_cl.dte.receiver.response"].with_user(
            self.dte_manager
        )

    def _server(self):
        return (
            self.env["fetchmail.server"]
            .sudo()
            .create(
                {
                    "name": "DTE exchange mailbox",
                    "server": "imap.example.com",
                    "server_type": "imap",
                    "l10n_cl_dte_intake": True,
                    "l10n_cl_dte_company_id": self.company.id,
                    "l10n_cl_dte_user_id": self.dte_manager.id,
                }
            )
        )

    @staticmethod
    def _message(attachments):
        message = EmailMessage()
        message["From"] = "dte@receiver.example"
        message["Subject"] = "Respuesta DTE"
        message.set_content("Adjunto respuesta")
        for name, content in attachments:
            message.add_attachment(
                content, maintype="application", subtype="xml", filename=name
            )
        return message.as_bytes()

    def _fetch(self, *attachment_lists):
        server = self._server()
        messages = {
            uid: self._message(attachments)
            for uid, attachments in enumerate(attachment_lists, 1)
        }
        imap = MagicMock()

        def uid(command, *args):
            if command == "search":
                return "OK", [b" ".join(str(key).encode() for key in messages)]
            return "OK", [(b"header", messages[int(args[0])])]

        imap.uid.side_effect = uid
        with patch.object(type(server), "connect", return_value=imap):
            server.fetch_mail()
        return self.env["l10n_cl.dte.receiver.response"].search([])

    def test_emailed_result_is_linked_to_our_dte_once(self):
        document, move = self.issued_document(folio=601)
        content = self.respuesta_dte(601)
        response = self._fetch([("respuesta.xml", content), ("notes.txt", b"x")])
        self.assertEqual(len(response), 1)
        self.assertEqual(
            (response.kind, response.match_state, response.responder_vat),
            ("respuesta_dte", "matched", "76234567-6"),
        )
        self.assertEqual(response.document_ids, document)
        event = document.receiver_event_ids
        self.assertEqual(
            (event.source, event.event_code, event.response_id),
            ("email", "RESULT_ACCEPTED", response),
        )
        self.assertEqual(document.receiver_state, "accepted")
        self.assertEqual(move.dte_receiver_state, "accepted")
        # The same file again, by mail or by hand, is recorded once.
        self.assertEqual(self.responses._import_xml(content, "again.xml"), response)
        self.assertEqual(len(document.receiver_event_ids), 1)
        self.assertFalse(self.env["l10n_cl.incoming.dte.import"].search([]))

    def test_reception_answer_is_history_only(self):
        document, _move = self.issued_document(folio=602)
        self.responses._import_xml(self.respuesta_dte(602, reception=True), "r.xml")
        self.assertEqual(document.receiver_event_ids.event_code, "RECEPTION_OK")
        self.assertEqual(document.receiver_state, "pending")

    def test_goods_receipt_is_verified_and_linked(self):
        document, _move = self.issued_document(folio=603)
        response = self.responses._import_xml(self.envio_recibos(603), "recibos.xml")
        self.assertEqual(
            (response.kind, response.match_state), ("envio_recibos", "matched")
        )
        event = document.receiver_event_ids
        self.assertEqual(
            (event.event_code, event.actor_vat), ("GOODS_RECEIPT", "12345678-5")
        )
        self.assertEqual(document.receiver_state, "goods_received")

    def test_registry_events_outrank_emailed_answers(self):
        document, move = self.issued_document(folio=604)
        self.responses._import_xml(self.respuesta_dte(604, state="2"), "rej.xml")
        self.assertEqual(document.receiver_state, "rejected")
        self.assertEqual(len(move.activity_ids), 1)
        with self.mock_registry(
            return_value=self.registry_answer(("ACD", "2026-01-19 10:00:00"))
        ):
            document.with_user(self.dte_manager).action_check_receiver_response()
        self.assertEqual(document.receiver_state, "accepted")

    def test_responses_for_other_dtes_are_flagged(self):
        self.issued_document(folio=605)
        unknown = self.responses._import_xml(self.respuesta_dte(9999), "x.xml")
        self.assertEqual(unknown.match_state, "unmatched")
        self.assertIn("9999", unknown.diagnostics)
        self.assertFalse(unknown.event_ids)
        foreign = self.responses._import_xml(
            self.respuesta_dte(605, recipient="11111111-1"), "y.xml"
        )
        self.assertEqual(foreign.match_state, "unmatched")
        self.assertIn("11111111-1", foreign.diagnostics)
        self.assertFalse(self.env["l10n_cl.dte.receiver.event"].search([]))

    def test_bad_signature_is_refused(self):
        document, _move = self.issued_document(folio=606)
        forged = self.respuesta_dte(606, state="2").replace(
            b"<EstadoDTE>2</EstadoDTE>", b"<EstadoDTE>0</EstadoDTE>"
        )
        with self.assertRaisesRegex(ValidationError, "digest"):
            self.responses._import_xml(forged, "forged.xml")
        receipt = self.envio_recibos(606).replace(
            b"Bodega sintetica", b"Bodega alterada"
        )
        with self.assertRaises(ValidationError):
            self.responses._import_xml(receipt, "forged-receipt.xml")
        self.assertFalse(self._fetch([("forged.xml", forged)]))
        self.assertFalse(document.receiver_event_ids)

    def test_access_and_companies(self):
        document, _move = self.issued_document(folio=607)
        content = self.respuesta_dte(607)
        with self.assertRaises(AccessError):
            self.env["l10n_cl.dte.receiver.response"].with_user(
                self.dte_user
            )._import_xml(content, "user.xml")
        response = self.responses._import_xml(content, "ok.xml")
        as_user = response.with_user(self.dte_user)
        self.assertEqual(as_user.match_state, "matched")
        with self.assertRaises(AccessError):
            as_user.write({"match_state": "unmatched"})
        other = self.env["res.company"].sudo().create({"name": "Other receiver co"})
        outsider = new_test_user(
            self.env(su=True),
            login="response-outsider",
            groups="base.group_user,l10n_cl_dte_core.group_dte_user",
            company_id=other.id,
            company_ids=[Command.set(other.ids)],
        )
        self.assertFalse(
            self.env["l10n_cl.dte.receiver.response"]
            .with_user(outsider)
            .with_context(allowed_company_ids=other.ids)
            .search([])
        )
        self.assertTrue(document.receiver_event_ids)
