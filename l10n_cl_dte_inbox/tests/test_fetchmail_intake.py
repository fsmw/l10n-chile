# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hashlib
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase

from .test_incoming_document import TestIncomingDte


class TestFetchmailIntake(TransactionCase):
    """A DTE mailbox imports new XML attachments once, by IMAP UID."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        manager = cls.env.ref("l10n_cl_dte_core.group_dte_manager")
        cls.env.user.sudo().write({"groups_id": [(4, manager.id)]})
        cls.company.partner_id.vat = "66666666-6"

    _xml = classmethod(TestIncomingDte._xml.__func__)

    def _server(self):
        return (
            self.env["fetchmail.server"]
            .sudo()
            .create(
                {
                    "name": "DTE mailbox",
                    "server": "imap.example.com",
                    "server_type": "imap",
                    "l10n_cl_dte_intake": True,
                    "l10n_cl_dte_company_id": self.company.id,
                    "l10n_cl_dte_user_id": self.env.user.id,
                }
            )
        )

    def _message(self, attachments):
        message = EmailMessage()
        message["From"] = "supplier@example.com"
        message["Subject"] = "DTE"
        message.set_content("Adjunto DTE")
        for name, content in attachments:
            message.add_attachment(
                content, maintype="application", subtype="xml", filename=name
            )
        return message.as_bytes()

    def _imap(self, messages):
        imap = MagicMock()

        def uid(command, *args):
            if command == "search":
                return "OK", [b" ".join(str(key).encode() for key in messages)]
            return "OK", [(b"header", messages[int(args[0])])]

        imap.uid.side_effect = uid
        return imap

    def test_new_attachments_are_imported_once(self):
        server = self._server()
        messages = {
            7: self._message([("dte.xml", self._xml()), ("notes.txt", b"x")]),
            8: self._message([("respuesta.xml", b"<RespuestaDTE/>")]),
        }
        imap = self._imap(messages)
        with patch.object(type(server), "connect", return_value=imap):
            server.fetch_mail()
            server.fetch_mail()
        imports = self.env["l10n_cl.incoming.dte.import"].search(
            [("channel", "=", "email")]
        )
        self.assertEqual(len(imports), 1)
        self.assertEqual(imports.xml_sha256, hashlib.sha256(self._xml()).hexdigest())
        self.assertEqual(server.l10n_cl_dte_last_uid, 8)
        fetches = [call for call in imap.uid.call_args_list if call.args[0] == "fetch"]
        self.assertEqual(len(fetches), 2)
        self.assertEqual(fetches[0].args[2], "(BODY.PEEK[])")

    def test_intake_needs_an_imap_dte_manager_setup(self):
        with self.assertRaises(ValidationError):
            self.env["fetchmail.server"].sudo().create(
                {
                    "name": "Bad",
                    "server": "pop.example.com",
                    "server_type": "pop",
                    "l10n_cl_dte_intake": True,
                }
            )
