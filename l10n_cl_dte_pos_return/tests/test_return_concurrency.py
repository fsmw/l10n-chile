# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

from psycopg2.errors import SerializationFailure

from odoo import sql_db
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import mute_logger
from odoo.tools import SQL


@tagged("post_install", "-at_install", "dte_pos_return")
class TestPosReturnConcurrency(TransactionCase):
    def test_shared_root_requires_retry_for_concurrent_adjustments(self):
        """Exercise production locking in real PostgreSQL transactions."""
        connection = sql_db.db_connect(self.env.cr.dbname)
        source_key = "pos-return-lock-test-" + uuid4().hex
        company_id = self.env.ref("base.main_company").id
        # Raw artifact-free draft metadata avoids lifecycle audit events.
        # This fixture exercises row locking only, not document creation.
        with connection.cursor() as cursor:
            cursor.execute(
                SQL(
                    "INSERT INTO l10n_cl_electronic_document "
                    "(name, company_id, sii_environment, document_code, source_key, "
                    "preparation_state, fiscal_state, note_revision) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    "Synthetic concurrency metadata",
                    company_id,
                    "demo",
                    "39",
                    source_key,
                    "draft",
                    "not_submitted",
                    0,
                )
            )
            document_id = cursor.fetchone()[0]
        barrier = Barrier(2)
        lock = type(self.env["l10n_cl.electronic.document"])._lock_note_chain

        def adjust():
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        SQL(
                            (
                                "SELECT note_revision FROM l10n_cl_electronic_document "
                                "WHERE id = %s"
                            ),
                            document_id,
                        )
                    )
                    self.assertEqual(cursor.fetchone()[0], 0)
                    # ORM cache misses acquire the test registry's mutex.
                    # A facade runs the actual production SQL without that
                    # mutex; separate workflow tests cover authorization.
                    root = SimpleNamespace(
                        id=document_id,
                        ids=[document_id],
                        company_id=company_id,
                        note_root_document_id=False,
                        env=SimpleNamespace(cr=cursor, companies={company_id}),
                        ensure_one=lambda: None,
                        _check_note_chain_operator=lambda *args, **kwargs: None,
                        invalidate_recordset=lambda *args: None,
                    )
                    barrier.wait(timeout=10)
                    lock(root, root)
                return "serialized"
            except SerializationFailure:
                return "retry"

        try:
            with mute_logger("odoo.sql_db"), ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(adjust) for _index in range(2)]
                results = [future.result(timeout=20) for future in futures]
            self.assertCountEqual(results, ["serialized", "retry"])
            with connection.cursor() as cursor:
                cursor.execute(
                    SQL(
                        (
                            "SELECT note_revision FROM l10n_cl_electronic_document "
                            "WHERE id = %s"
                        ),
                        document_id,
                    )
                )
                self.assertEqual(cursor.fetchone()[0], 1)
        finally:
            # unlink is intentionally forbidden. Remove only our exact
            # artifact-free draft metadata, never an existing fiscal record.
            with connection.cursor() as cursor:
                cursor.execute(
                    SQL(
                        "DELETE FROM l10n_cl_electronic_document "
                        "WHERE id = %s AND source_key = %s AND preparation_state = %s",
                        document_id,
                        source_key,
                        "draft",
                    )
                )
