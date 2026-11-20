# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import ValidationError

from ..services.caf_availability import available_folios
from .common import CafRequestTestCase, allocation_pages


class TestCafReplenishment(CafRequestTestCase):
    def rule(self, **values):
        return self.Rule.with_user(self.dte_manager).create(
            {
                "document_code": "33",
                "environment": "certification",
                "threshold": 3,
                "request_quantity": 10,
                **values,
            }
        )

    def test_availability_comes_from_validated_cafs_and_reservations(self):
        authorization = self.import_caf(1, 5)
        self.import_caf(1, 5, document_code="61")
        self.assertEqual(
            available_folios(self.env, self.company, "33", "certification"), 5
        )
        document = self.create_document(document_code="33")
        authorization.with_user(self.dte_manager).action_reserve_folio(
            document, "replenishment-test-1"
        )
        self.assertEqual(
            available_folios(self.env, self.company, "33", "certification"), 4
        )
        self.assertEqual(
            available_folios(self.env, self.company, "33", "production"), 0
        )
        self.assertEqual(self.rule().available_folios, 4)

    def test_operator_rule_queues_pending_request_never_executed(self):
        rule = self.rule()
        with self.sii(*self.success()) as transport:
            created = self.Rule._cron_scan_rules()
            self.assertEqual(created.rule_id, rule)
            self.assertEqual(created.state, "pending")
            self.assertEqual(created.availability_snapshot, 0)
            self.assertEqual(created.requested_quantity, 10)
            self.Request._cron_process_requests()
            self.assertEqual(created.state, "pending")
            # An outstanding request prevents a duplicate.
            self.assertFalse(self.Rule._cron_scan_rules())
            self.assertEqual(rule.last_scan_state, "outstanding")
        self.assertFalse(transport.calls)

    def test_automatic_rule_runs_only_from_scheduled_job(self):
        rule = self.rule(
            approval_mode="automatic", execution_user_id=self.dte_manager.id
        )
        with self.sii(*self.success(1, 10)) as transport:
            rule.with_user(self.dte_manager).action_scan()
            request = rule.last_request_id
            self.assertEqual(request.state, "approved")
            self.assertFalse(transport.calls)
            self.Request._cron_process_requests()
        self.assertEqual(len(transport.assistant_calls), 5)
        self.assertEqual(request.state, "success")
        self.assertEqual(request.attempt_ids.executed_by, self.dte_manager)
        self.assertEqual(rule.available_folios, 10)
        self.assertEqual(rule.with_user(self.dte_manager).action_scan(), True)
        self.assertEqual(rule.last_scan_state, "above_threshold")

    def test_scheduled_job_retries_pre_dispatch_failures_only(self):
        rule = self.rule(
            approval_mode="automatic", execution_user_id=self.dte_manager.id
        )
        lost = RuntimeError("synthetic lost response")
        pages = allocation_pages(1, 10)
        with self.sii(lost, *pages[:3], lost) as transport:
            self.Rule._cron_scan_rules()
            request = rule.last_request_id
            self.Request._cron_process_requests()
            self.assertEqual(request.state, "failed")
            # A failed automatic request stops new requests for the rule.
            self.assertFalse(self.Rule._cron_scan_rules())
            self.assertEqual(rule.last_scan_state, "stopped")
            self.Request._cron_process_requests()
            self.assertEqual(request.state, "uncertain")
            self.Request._cron_process_requests()
        self.assertEqual(len(transport.assistant_calls), 5)

    def test_threshold_and_cooldown(self):
        self.import_caf(1, 20)
        rule = self.rule()
        self.assertFalse(self.Rule._cron_scan_rules())
        self.assertEqual(rule.last_scan_state, "above_threshold")
        rule.threshold = 30
        request = self.Rule._cron_scan_rules()
        request.with_user(self.dte_manager).action_cancel()
        self.assertFalse(self.Rule._cron_scan_rules())
        self.assertEqual(rule.last_scan_state, "cooldown")
        rule.cooldown_hours = 0
        self.assertTrue(self.Rule._cron_scan_rules())

    def test_environment_mismatch_is_not_scanned(self):
        rule = self.rule(environment="production")
        self.assertFalse(self.Rule._cron_scan_rules())
        self.assertEqual(rule.last_scan_state, "error")

    def test_automatic_execution_requires_a_dte_manager(self):
        with self.assertRaises(ValidationError):
            self.rule(approval_mode="automatic")
        with self.assertRaises(ValidationError):
            self.rule(approval_mode="automatic", execution_user_id=self.dte_user.id)
