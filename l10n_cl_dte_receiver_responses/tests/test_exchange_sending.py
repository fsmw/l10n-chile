# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_gateway.services.claim_registry import (
    SiiClaimRegistryClient,
)

from .common import SUPPLIER_EMAIL, ExchangeCase


@tagged("post_install", "-at_install")
class TestExchangeSending(ExchangeCase):
    def _mail(self, exchange):
        return self.env["mail.mail"].sudo().browse(exchange.sudo().mail_id.id)

    def _accepted_registry_action(self, document, code):
        response = self.answering_env[
            "l10n_cl.incoming.dte.response"
        ]._create_from_document(
            document, code, "Synthetic review", "Synthetic contract"
        )
        answer = {
            "code": 0,
            "description": "Accion Completada OK",
            "success": True,
            "raw_response": b"<answer/>",
        }
        with patch.object(
            SiiClaimRegistryClient, "register_action", return_value=answer
        ):
            # The inbox job writes its own records; run it as the queue would
            # run a job that is allowed to update the registry response.
            response.sudo().with_context(queue_job__no_delay=True).action_enqueue()
        self.assertEqual(response.state, "accepted")
        return response

    def test_email_attaches_the_exact_signed_xml_to_the_supplier(self):
        batch, _documents = self.receive(1010)
        action = batch.action_send_reception_response()
        exchange = self.exchanges.browse(action["res_id"])
        self.assertEqual(exchange.recipient_email, SUPPLIER_EMAIL)
        self.assertEqual(exchange.state, "queued")
        self.assertEqual(exchange.send_count, 1)
        mail = self._mail(exchange)
        self.assertEqual(mail.email_to, SUPPLIER_EMAIL)
        self.assertEqual(len(mail.attachment_ids), 1)
        attachment = mail.attachment_ids
        self.assertEqual(attachment.name, exchange.filename)
        self.assertEqual(attachment.raw, self.xml_of(exchange))
        self.assertEqual(
            hashlib.sha256(attachment.raw).hexdigest(), exchange.xml_sha256
        )
        mail.write({"state": "exception", "failure_reason": "Synthetic bounce"})
        self.assertEqual(exchange.state, "failed")
        self.assertEqual(exchange.send_error, "Synthetic bounce")
        mail.write({"state": "sent"})
        self.assertEqual(exchange.state, "sent")

    def test_response_without_address_stays_signed_until_corrected(self):
        self.supplier_partner.sudo().email = False
        self.company.partner_id.sudo().email = False
        batch, _documents = self.receive(1015)
        action = batch.action_send_reception_response()
        exchange = self.exchanges.browse(action["res_id"])
        self.assertFalse(exchange.recipient_email)
        self.assertEqual(exchange.state, "signed")
        with self.assertRaises(UserError):
            exchange.action_send()
        exchange.write({"recipient_email": "fixed@supplier.example.com"})
        exchange.action_send()
        self.assertEqual(self._mail(exchange).email_to, "fixed@supplier.example.com")
        with self.assertRaises(UserError):
            exchange.write({"recipient_email": "late@supplier.example.com"})

    def test_partner_exchange_mailbox_is_preferred_when_available(self):
        if "l10n_cl_dte_email" not in self.supplier_partner._fields:
            self.skipTest("The partner DTE exchange email needs l10n_cl_dte_delivery.")
        self.supplier_partner.sudo().l10n_cl_dte_email = (
            "intercambio@supplier.example.com"
        )
        _batch, documents = self.receive(1020)
        exchange = self.exchanges._prepare_documents("result", documents)
        self.assertEqual(exchange.recipient_email, "intercambio@supplier.example.com")

    def test_one_response_per_kind_and_explicit_resend(self):
        batch, documents = self.receive(1030)
        first = self.exchanges._prepare_documents("result", documents)
        self.assertEqual(
            self.exchanges._prepare_documents("result", documents, state="2"), first
        )
        self.assertEqual(
            self.exchanges._prepare_reception(batch),
            self.exchanges._prepare_reception(batch),
        )
        content = self.xml_of(first)
        first.action_send()
        with self.assertRaises(UserError):
            first.action_send()
        first_mail = first.mail_id
        first.action_resend()
        self.assertEqual(first.send_count, 2)
        self.assertNotEqual(first.mail_id, first_mail)
        self.assertEqual(self._mail(first).attachment_ids.raw, content)
        self.assertEqual(self.xml_of(first), content)
        self.assertEqual(len(self.exchanges.search([("kind", "=", "result")])), 1)
        with self.assertRaises(UserError):
            first.write({"supplier_vat": "1-9"})
        with self.assertRaises(UserError):
            first.unlink()

    def test_wizard_groups_by_supplier_and_signs_once(self):
        _batch, documents = self.receive(1040, 1050)
        wizard = (
            self.answering_env["l10n_cl.incoming.dte.exchange.wizard"]
            .with_context(active_model="l10n_cl.incoming.dte", active_ids=documents.ids)
            .create({"kind": "receipt", "site": "Bodega central"})
        )
        self.assertEqual(wizard.document_ids, documents)
        action = wizard.action_confirm()
        exchange = self.exchanges.browse(action["res_id"])
        self.assertEqual(exchange.kind, "receipt")
        self.assertEqual(exchange.state, "queued")
        self.assertEqual(exchange.line_ids.incoming_document_id, documents)

    def test_registry_action_only_offers_without_the_setting(self):
        _batch, documents = self.receive(1060)
        response = self._accepted_registry_action(documents, "ACD")
        self.assertFalse(self.exchanges.search([]))
        self.assertEqual(response.exchange_kind, "result")
        action = response.action_open_exchange_wizard()
        self.assertEqual(action["context"]["default_result_state"], "0")
        self.assertEqual(action["context"]["default_registry_response_id"], response.id)

    def test_registry_action_auto_sends_when_enabled(self):
        self.recipient.sudo().l10n_cl_dte_exchange_auto_send = True
        _batch, documents = self.receive(1070, 1080)
        claimed, received = documents.sorted("folio")
        self._accepted_registry_action(claimed, "RCD")
        self._accepted_registry_action(received, "ERM")
        result = self.exchanges.search([("kind", "=", "result")])
        receipt = self.exchanges.search([("kind", "=", "receipt")])
        self.assertEqual(result.line_ids.status_code, "2")
        self.assertEqual(result.line_ids.gloss, "Synthetic review")
        self.assertEqual(result.trigger, "registry_auto")
        self.assertEqual(result.state, "queued")
        self.assertEqual(receipt.line_ids.incoming_document_id, received)
        self.assertEqual(receipt.state, "queued")
        self.assertTrue(result.registry_response_id)

    def test_auto_send_failure_keeps_the_registry_result(self):
        self.recipient.sudo().l10n_cl_dte_exchange_auto_send = True
        _batch, documents = self.receive(1090)
        self.recipient_profile.sudo().active = False
        logger = "odoo.addons.l10n_cl_dte_receiver_responses.models.incoming_response"
        with self.assertLogs(logger, "WARNING"):
            response = self._accepted_registry_action(documents, "ACD")
        self.assertEqual(response.state, "accepted")
        self.assertFalse(self.exchanges.search([]))
        self.assertIn(
            "exchange_auto_send_failed", documents.event_ids.mapped("event_code")
        )

    def test_dte_user_reads_but_cannot_send(self):
        _batch, documents = self.receive(1100)
        exchange = self.exchanges._prepare_documents("result", documents)
        as_user = exchange.with_user(self.dte_user)
        self.assertEqual(as_user.read(["state"])[0]["state"], "signed")
        self.assertTrue(
            base64.b64decode(as_user.with_context(bin_size=False).signed_xml)
        )
        with self.assertRaises(AccessError):
            as_user.action_send()
        with self.assertRaises(AccessError):
            self.exchanges.with_user(self.dte_user)._prepare_documents(
                "receipt", documents
            )

    def test_other_company_cannot_see_responses(self):
        _batch, documents = self.receive(1110)
        exchange = self.exchanges._prepare_documents("result", documents)
        other_env = self.env(
            context=dict(self.env.context, allowed_company_ids=[self.company.id])
        )
        self.assertFalse(
            other_env["l10n_cl.incoming.dte.exchange"].search(
                [("id", "=", exchange.id)]
            )
        )
        self.assertFalse(
            other_env["l10n_cl.incoming.dte.exchange.line"].search(
                [("exchange_id", "=", exchange.id)]
            )
        )
