# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import datetime
from uuid import uuid4

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_core.tests.common import DteTestCase

ATTEMPT = "l10n_cl.submission.attempt"
AUDIT_FIELDS = (
    "state response_digest completed_at submission_id "
    "operation request_digest started_at"
).split()


@tagged("post_install", "-at_install")
class TestSubmissionAttempt(DteTestCase):
    def attempt(self):
        submissions = self.env["l10n_cl.electronic.submission"].sudo()
        submission = submissions.create(
            dict(document_id=self.create_document().id, idempotency_key=uuid4().hex)
        )
        attempts = self.env[ATTEMPT].sudo()
        return attempts.create(
            dict(submission_id=submission.id, operation="send", request_digest="a" * 64)
        )

    def test_stale_started_cache_cannot_replace_completed_audit(self):
        for method in ("write", "_complete"):
            with self.subTest(method=method):
                attempt = self.attempt()
                attempt.flush_recordset()
                self.assertEqual(attempt.state, "started")
                # Simulate a winner behind the ORM cache, without a new SQL snapshot.
                self.cr.execute(
                    "UPDATE l10n_cl_submission_attempt SET state = %s, "
                    "response_digest = %s, completed_at = %s WHERE id = %s",
                    ["completed", "b" * 64, datetime(2026, 1, 1), attempt.id],
                )
                with self.assertRaises(UserError):
                    if method == "write":
                        attempt.write({"state": "failed", "response_digest": "c" * 64})
                    else:
                        attempt._complete("unknown", response_digest="c" * 64)
                attempt.invalidate_recordset()
                self.assertEqual(attempt.state, "completed")
                self.assertEqual(attempt.response_digest, "b" * 64)
                self.assertEqual(attempt.completed_at, datetime(2026, 1, 1))
                self.assertEqual(attempt.request_digest, "a" * 64)

    def test_request_identity_and_terminal_audit_are_immutable(self):
        attempt = self.attempt()
        identity = "submission_id company_id operation request_digest started_at"
        for field in identity.split():
            with self.subTest(field=field), self.assertRaises(UserError):
                attempt.write({"state": "completed", field: False})
        self.assertEqual(attempt.state, "started")
        for state in ("completed", "failed", "unknown"):
            attempt = self.attempt()
            attempt._complete(state, response_digest="b" * 64)
            before = attempt.read(AUDIT_FIELDS)
            with self.assertRaises(UserError):
                attempt.write({"state": "failed", "response_digest": "c" * 64})
            with self.assertRaises(UserError):
                attempt._complete("unknown", response_digest="c" * 64)
            attempt.invalidate_recordset()
            self.assertEqual(attempt.read(AUDIT_FIELDS), before)

    def test_batch_completion_is_all_or_nothing(self):
        started, terminal = self.attempt(), self.attempt()
        terminal._complete("completed", response_digest="b" * 64)
        for records in (started | terminal, terminal | started):
            with self.assertRaises(UserError):
                records.write({"state": "failed", "response_digest": "c" * 64})
            records.invalidate_recordset()
            self.assertEqual(started.state, "started")
            self.assertFalse(started.completed_at)
            self.assertFalse(started.response_digest)
            self.assertEqual(terminal.response_digest, "b" * 64)
        batch = started | self.attempt()
        batch.write({"state": "completed", "response_digest": "d" * 64})
        self.assertEqual(batch.mapped("state"), ["completed", "completed"])

    def test_completion_preserves_write_acl_and_record_rules(self):
        attempt = self.attempt()
        for user in (self.dte_user, self.dte_manager):
            record = attempt.with_user(user)
            if user == self.dte_manager:
                self.assertTrue(record.has_access("write"))
                self.env["ir.rule"].sudo().create(
                    {
                        "name": "Deny attempt completion",
                        "model_id": self.env["ir.model"]._get_id(ATTEMPT),
                        "domain_force": repr([("id", "!=", attempt.id)]),
                        "perm_read": False,
                        "perm_write": True,
                        "perm_create": False,
                        "perm_unlink": False,
                    }
                )
            self.assertTrue(record.has_access("read"))
            self.assertFalse(record.has_access("write"))
            with self.assertRaises(AccessError):
                record.write({"state": "completed"})
            with self.assertRaises(AccessError):
                record._complete("completed")
        attempt.invalidate_recordset()
        self.assertEqual(attempt.state, "started")
        self.assertFalse(attempt.completed_at)
