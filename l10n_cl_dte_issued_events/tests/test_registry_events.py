# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import datetime, timedelta

from freezegun import freeze_time

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import new_test_user

from odoo.addons.l10n_cl_dte_gateway.services.certification_auth import (
    SiiTransportError,
)

from .common import RECEPTION, ReceiverEventsCase

START = datetime.fromisoformat(RECEPTION)


@tagged("post_install", "-at_install")
class TestRegistryEvents(ReceiverEventsCase):
    """SII registry events of our issued DTEs and the derived receiver state."""

    def _check(self, document, answer, at=START + timedelta(hours=2)):
        with freeze_time(at), self.mock_registry(return_value=answer) as call:
            document.with_user(self.dte_manager).action_check_receiver_response()
        return call

    def test_acceptance_is_stored_once_and_shown_on_the_invoice(self):
        document, move = self.issued_document(folio=501)
        answer = self.registry_answer(("ACD", "2026-01-16 10:00:00"))
        call = self._check(document, answer)
        self._check(document, answer, at=START + timedelta(hours=4))
        company, vat, code, folio = call.call_args.args
        self.assertEqual(
            (company, vat, code, folio), (self.company, "76864280-K", "33", 501)
        )
        event = document.receiver_event_ids
        self.assertEqual(len(event), 1)
        self.assertEqual(
            (event.source, event.event_code, event.actor_vat),
            ("sii", "ACD", "76234567-6"),
        )
        # Chile summer time is UTC-3.
        self.assertEqual(event.event_date, datetime(2026, 1, 16, 13, 0))
        self.assertEqual(document.receiver_state, "accepted")
        self.assertEqual(move.dte_receiver_state, "accepted")
        self.assertEqual(document.receiver_check_count, 2)
        self.assertFalse(move.activity_ids)

    def test_goods_receipt_then_acceptance_finishes_tracking(self):
        document, _move = self.issued_document(folio=502)
        self._check(document, self.registry_answer(("ERM", "2026-01-16 10:00:00")))
        self.assertEqual(document.receiver_state, "goods_received")
        self.assertFalse(document.receiver_tracking_done)
        self._check(
            document,
            self.registry_answer(
                ("ERM", "2026-01-16 10:00:00"), ("ACD", "2026-01-17 10:00:00")
            ),
        )
        self.assertEqual(len(document.receiver_event_ids), 2)
        self.assertEqual(document.receiver_state, "accepted")
        self.assertTrue(document.receiver_tracking_done)
        self.assertFalse(document.receiver_next_check_at)

    def test_claim_schedules_one_activity_and_never_changes_accounting(self):
        document, move = self.issued_document(folio=503)
        amount = move.amount_total
        answer = self.registry_answer(("RCD", "2026-01-16 10:00:00"))
        self._check(document, answer)
        self._check(document, answer, at=START + timedelta(hours=5))
        self.assertEqual(document.receiver_state, "claimed_content")
        self.assertEqual(move.dte_receiver_state, "claimed_content")
        self.assertEqual(len(move.activity_ids), 1)
        self.assertIn("RCD", move.activity_ids.summary)
        self.assertEqual(
            move.activity_ids.user_id, move.invoice_user_id or move.create_uid
        )
        self.assertEqual((move.state, move.amount_total), ("posted", amount))
        self.assertTrue(document.receiver_tracking_done)

    def test_partial_and_total_lack_of_goods_are_claims(self):
        document, move = self.issued_document(folio=504)
        self._check(document, self.registry_answer(("RFP", "2026-01-16 10:00:00")))
        self.assertEqual(document.receiver_state, "claimed_partial")
        self._check(
            document,
            self.registry_answer(
                ("RFP", "2026-01-16 10:00:00"), ("RFT", "2026-01-17 08:00:00")
            ),
        )
        self.assertEqual(document.receiver_state, "claimed_total")
        self.assertEqual(len(move.activity_ids), 2)

    def test_eight_days_without_claim_is_tacit_acceptance(self):
        document, move = self.issued_document(folio=505)
        with freeze_time(START + timedelta(days=7, hours=23)):
            document.invalidate_recordset(["receiver_state"])
            self.assertEqual(document.receiver_state, "pending")
        with freeze_time(START + timedelta(days=8, minutes=1)):
            document.invalidate_recordset(["receiver_state"])
            move.invalidate_recordset(["dte_receiver_state"])
            self.assertEqual(document.receiver_state, "tacit")
            self.assertEqual(move.dte_receiver_state, "tacit")
        self.assertEqual(document.receiver_deadline, START + timedelta(days=8))

    def test_cron_polls_the_window_with_backoff_then_stops(self):
        document, _move = self.issued_document(folio=506)
        old, _old_move = self.issued_document(folio=507, at=START - timedelta(days=20))
        documents = self.env["l10n_cl.electronic.document"]
        answer = self.registry_answer()
        checks = []

        def cron(at):
            with freeze_time(at), self.mock_registry(return_value=answer) as call:
                documents._cron_check_receiver_responses()
            checks.append([args.args[3] for args in call.call_args_list])

        cron(START + timedelta(minutes=5))
        self.assertEqual(
            document.receiver_next_check_at, START + timedelta(hours=1, minutes=5)
        )
        cron(START + timedelta(minutes=30))
        cron(START + timedelta(hours=1, minutes=5))
        self.assertEqual(
            document.receiver_next_check_at, START + timedelta(hours=3, minutes=5)
        )
        cron(START + timedelta(days=7, hours=23))
        self.assertEqual(document.receiver_next_check_at, START + timedelta(days=8))
        cron(START + timedelta(days=8))
        self.assertTrue(document.receiver_tracking_done)
        cron(START + timedelta(days=8, hours=2))
        self.assertEqual(checks, [[506], [], [506], [506], [506], []])
        self.assertFalse(old.receiver_check_count)

    def test_registry_failure_is_recorded_and_backs_off(self):
        document, _move = self.issued_document(folio=508)
        lost = SiiTransportError("Synthetic outage", dispatched=True)
        with (
            freeze_time(START + timedelta(hours=1)),
            self.mock_registry(side_effect=lost),
        ):
            self.env["l10n_cl.electronic.document"]._cron_check_receiver_responses()
            self.assertEqual(document.receiver_check_message, "Synthetic outage")
            self.assertEqual(document.receiver_check_count, 1)
            self.assertEqual(
                document.receiver_next_check_at, START + timedelta(hours=2)
            )
            self.assertFalse(document.receiver_event_ids)
            with self.assertRaisesRegex(UserError, "Synthetic outage"):
                document.with_user(self.dte_manager).action_check_receiver_response()

    def test_untracked_documents_are_not_queried(self):
        note, _move = self.issued_document(folio=509, code="61", with_move=False)
        self.assertFalse(note.receiver_tracked)
        self.assertEqual(note.receiver_state, "none")
        with self.mock_registry(return_value=self.registry_answer()) as call:
            self.env["l10n_cl.electronic.document"]._cron_check_receiver_responses()
            with self.assertRaises(UserError):
                note.with_user(self.dte_manager).action_check_receiver_response()
        call.assert_not_called()

    def test_users_read_managers_query_and_companies_are_isolated(self):
        document, _move = self.issued_document(folio=510)
        self._check(document, self.registry_answer(("ACD", "2026-01-16 10:00:00")))
        with self.assertRaises(AccessError):
            document.with_user(self.dte_user).action_check_receiver_response()
        events = self.env["l10n_cl.dte.receiver.event"].with_user(self.dte_user)
        self.assertEqual(events.search([]).event_code, "ACD")
        with self.assertRaises(AccessError):
            events.search([]).write({"description": "forged"})
        other = self.env["res.company"].sudo().create({"name": "Other DTE company"})
        outsider = new_test_user(
            self.env(su=True),
            login="receiver-outsider",
            groups="base.group_user,l10n_cl_dte_core.group_dte_manager",
            company_id=other.id,
            company_ids=[Command.set(other.ids)],
        )
        self.assertFalse(
            self.env["l10n_cl.dte.receiver.event"]
            .with_user(outsider)
            .with_context(allowed_company_ids=other.ids)
            .search([])
        )
        with self.assertRaises(AccessError):
            document.with_user(outsider).with_context(
                allowed_company_ids=other.ids
            ).action_check_receiver_response()
