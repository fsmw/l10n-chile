# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest import TestCase
from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import DteNoteTestCase


@tagged("post_install", "-at_install")
class TestNationalChainLock(DteNoteTestCase):
    def _create_note_document(self, document_code="61"):
        source, target, _snapshot = self.create_accepted_source()
        if document_code == "61":
            move = self.create_credit_note(source, target)
        else:
            move = self.make_invoice("56", post=False)
            move.write(
                {
                    **source._prepare_dte_note_values(target, "amount", "Correction"),
                    "debit_origin_id": source.id,
                    "l10n_latam_document_type_id": self.document_types["56"].id,
                }
            )
        move.action_post()
        note = target.create(
            {
                "name": "Synthetic national note lock",
                "company_id": target.company_id.id,
                "sii_environment": "demo",
                "document_code": document_code,
                "source_key": f"national-note-lock-{move.id}",
                "source_move_id": move.id,
                "note_target_document_id": target.id,
                "note_root_document_id": target.id,
            }
        )
        return note, target

    def test_note_prefix_locks_existing_chain_and_invalidates_cache(self):
        note, target = self._create_note_document()
        revision = target.note_revision
        self.assertEqual(target.fiscal_state, "accepted")
        target.flush_recordset()
        self.env.cr.execute(
            "UPDATE l10n_cl_electronic_document SET fiscal_state = %s WHERE id = %s",
            ["processing", target.id],
        )
        native_lock = type(note)._lock_note_chain
        calls = []

        def lock(records, reference, source=None):
            calls.append((records.ids, reference.id, source))
            return native_lock(records, reference, source=source)

        with patch.object(type(note), "_lock_note_chain", lock):
            note._lock_national_fiscal_chain()
        self.assertEqual(calls, [([note.id], target.id, None)])
        self.assertEqual(target.note_revision, revision + 1)
        self.assertEqual(target.fiscal_state, "processing")

    def test_prefix_is_dormant_and_non_note_is_unchanged(self):
        note, target = self._create_note_document()
        revision = target.note_revision
        with patch.object(type(note), "_lock_note_chain", side_effect=AssertionError):
            target._lock_national_fiscal_chain()
            note._lock_fiscal_chain()
        self.assertEqual(target.note_revision, revision)

    def test_outcome_application_locks_note_then_submission_then_attempt(self):
        submissions = self.env["l10n_cl.electronic.submission"].sudo()
        attempts = self.env["l10n_cl.submission.attempt"].sudo()
        for code in ("56", "61"):
            with self.subTest(code=code), self.cr.savepoint() as savepoint:
                note, target = self._create_note_document(code)
                submission = submissions.create(
                    {"document_id": note.id, "idempotency_key": f"note-lock-{note.id}"}
                ).with_user(self.dte_manager)
                attempt = attempts.create(
                    {"submission_id": submission.id, "operation": "send"}
                )
                revision, calls = target.note_revision, []

                def spy(model, method, label, case=(calls, target, revision)):
                    calls, target, revision = case
                    native = getattr(model, method)

                    def lock(records, *args, **kwargs):
                        result = native(records, *args, **kwargs)
                        env = records.env
                        calls.append((label, records.ids, env.uid, env.su))
                        if label == "note":
                            self.assertEqual(args[0], target)
                            self.assertEqual(target.note_revision, revision + 1)
                        if label == "attempt":
                            raise RuntimeError("real lock reached")
                        return result

                    return patch.object(model, method, lock)

                with (
                    spy(type(note), "_lock_note_chain", "note"),
                    spy(type(submission), "_lock_production_chain", "submission"),
                    spy(type(attempt), "_lock_for_completion", "attempt"),
                    TestCase.assertRaisesRegex(self, RuntimeError, "real lock reached"),
                ):
                    submission._apply_production_outcome(attempt, {}, "send")
                actor = self.dte_manager.id
                self.assertEqual(
                    calls,
                    [
                        ("note", note.ids, actor, False),
                        ("submission", submission.ids, actor, False),
                        ("attempt", attempt.ids, actor, True),
                    ],
                )
                self.assertEqual(target.note_revision, revision)
                savepoint.rollback()

    def test_prefix_preserves_operator_and_company_checks(self):
        note, target = self._create_note_document()
        revision = target.note_revision
        with self.assertRaises(AccessError):
            note.with_user(self.dte_user)._lock_national_fiscal_chain()
        other = self.env.ref("base.main_company")
        with self.assertRaises(AccessError):
            note.with_context(
                allowed_company_ids=other.ids
            )._lock_national_fiscal_chain()
        self.assertEqual(target.note_revision, revision)
