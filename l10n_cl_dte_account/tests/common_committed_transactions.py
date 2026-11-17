# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from contextlib import ExitStack, contextmanager
from functools import wraps
from types import MappingProxyType
from unittest.mock import patch

from odoo import api, models, sql_db
from odoo.tools import SQL


def _patch(stack, target, **methods):
    for name, replacement in methods.items():
        stack.enter_context(patch.object(target, name, replacement))


class CommittedTransactions:
    """Serial, isolated-database tests only; not a sandbox for hostile callbacks.

    Own only ORM-created partners. Raw SQL writes, private cursor controls,
    postcommit writes and concurrent users of the database are unsupported.
    Cleanup never adopts dependencies; unlink policy is audited for this stack.
    """

    def __init__(self, case):
        self.registry = case.registry
        self._created = ()

    @property
    def owned(self):
        return MappingProxyType({"res.partner": frozenset(self._created)})

    @contextmanager
    def cursor(self):
        # registry.cursor() may return Odoo's shared TestCursor instead.
        cr = sql_db.db_connect(self.registry.db_name).cursor()
        cr.transaction = api.Transaction(self.registry)
        rollback, close = cr.rollback, cr.close
        try:
            yield cr
        finally:

            def forbidden():
                raise RuntimeError("Fixture owns cursor control during finalization")

            with patch.multiple(
                cr, commit=forbidden, rollback=forbidden, close=forbidden
            ):
                try:
                    rollback()  # Hooks run while public controls remain blocked.
                finally:
                    try:
                        cr._cnx.rollback()  # A failing hook must not skip SQL rollback.
                    finally:
                        cr.prerollback.clear()
                        cr.postrollback.clear()
                        # Rollback already completed; native close must not rerun hooks.
                        with patch.object(cr, "rollback", lambda: None):
                            close()

    @classmethod
    def _plain(cls, value, ancestors=frozenset()):
        if type(value) in (type(None), bool, int, float, str):
            return value
        if type(value) not in (dict, list, tuple) or id(value) in ancestors:
            raise TypeError("Only acyclic plain detached values are supported")
        ancestors = ancestors | {id(value)}
        if type(value) is dict:
            if any(type(key) is not str for key in value):
                raise TypeError("Detached dictionary keys must be strings")
            return {key: cls._plain(item, ancestors) for key, item in value.items()}
        return type(value)(cls._plain(item, ancestors) for item in value)

    def _guard_partners(self, patches, env, guarded):
        original_create = models.BaseModel._create
        partner = type(env["res.partner"])
        public_create, public_write = partner.create, partner.write
        public_unlink = partner.unlink
        pending = []

        def known():
            # OCB assigns data['record'] after INSERT, before nested child create.
            # These are in-flight allocations, not IDs discovered by searching.
            provisional = {
                id_
                for frame in pending
                for data in frame
                if (record := data.get("record"))
                for id_ in record.ids
            }
            return self.owned["res.partner"] | provisional

        def check_parent(values):
            parent_id = values.get("parent_id")
            if parent_id and parent_id not in known():
                raise RuntimeError("Fixture relation targets an unowned partner")

        def observe(records, values):
            pending.append(values)
            try:
                if records._name != "res.partner":
                    raise RuntimeError("Fixture create: unsupported model")
                created = original_create(records, values)
                self._created += tuple(created.ids)
                return created
            finally:
                pending.pop()

        def create(records, values):
            for vals in values:
                check_parent(records._add_missing_default_values(dict(vals)))
            return public_create(records, values)

        def check_mutation(records):
            if set(records.ids) - known():
                raise RuntimeError("Fixture cannot mutate an unowned partner")
            if records:
                self._audit_cleanup(records.env, tuple(known()))

        def write(records, values):
            check_mutation(records)
            check_parent(values)
            return public_write(records, values)

        def unlink(records):
            check_mutation(records)
            return public_unlink(records)

        _patch(patches, models.BaseModel, _create=guarded(observe, original_create))
        _patch(
            patches,
            partner,
            create=api.model_create_multi(guarded(create, public_create)),
            write=guarded(write, public_write),
            unlink=guarded(unlink, public_unlink),
        )

    def run(self, callback):
        # Keep observers and public guards installed until cursor finalization ends.
        with ExitStack() as patches, self.cursor() as cr:
            env = api.Environment(
                cr,
                1,
                {
                    "tracking_disable": True,
                    "mail_create_nolog": True,
                    "mail_create_nosubscribe": True,
                    "active_test": False,
                },
                su=True,
            )
            commit, flush = cr.commit, cr.flush
            postcommit = type(cr.postcommit).run
            poisoned, result = False, None

            def deny_control():
                nonlocal poisoned
                poisoned = True
                raise RuntimeError("Fixture owns cursor control")

            def guarded(method, original):
                @wraps(method)
                def wrapped(records, *args, **kwargs):
                    nonlocal poisoned
                    if (
                        records.env.registry is not self.registry
                        or records.env.cr is not cr
                    ):
                        return original(records, *args, **kwargs)
                    try:
                        return method(records, *args, **kwargs)
                    except BaseException:
                        poisoned = True
                        raise

                return wrapped

            def guarded_flush():
                nonlocal poisoned
                try:
                    flush()
                    self._plain(result)  # Precommit can mutate the returned container.
                except BaseException:
                    poisoned = True
                    raise
                if poisoned:
                    raise RuntimeError("Fixture transaction is poisoned")

            def no_postcommit_sql(*args, **kwargs):
                raise RuntimeError("Postcommit SQL is unsupported")

            def guarded_postcommit(callbacks):
                if callbacks is not cr.postcommit:
                    return postcommit(callbacks)
                with patch.object(cr, "execute", no_postcommit_sql):
                    return postcommit(callbacks)

            self._guard_partners(patches, env, guarded)
            _patch(
                patches,
                cr,
                commit=deny_control,
                rollback=deny_control,
                close=deny_control,
                flush=guarded_flush,
            )
            _patch(patches, type(cr.postcommit), run=guarded_postcommit)
            result = callback(env)
            self._plain(result)
            # The real commit calls guarded_flush immediately before SQL COMMIT.
            # Retain the append-only ledger even if a postcommit callback raises.
            commit()  # Explicit independent test transaction; exactly one commit.
            return self._plain(result)

    def _audit_cleanup(self, env, ids):
        # No generic model deletion. These generic references bypass SQL FKs.
        for model, field in (
            ("ir.attachment", "res_model"),
            ("ir.model.data", "model"),
            ("mail.message", "model"),
            ("mail.followers", "res_model"),
            ("mail.scheduled.message", "model"),
            ("mail.activity", "res_model"),
        ):
            # ORM attachment searches hide binary-field rows even in sudo mode.
            env.cr.execute(
                SQL(
                    "SELECT 1 FROM %s WHERE %s = %s AND res_id IN %s LIMIT 1",
                    SQL.identifier(env[model]._table),
                    SQL.identifier(field),
                    "res.partner",
                    ids,
                )
            )
            if env.cr.fetchone():
                raise RuntimeError(f"Cleanup blocked by unowned {model}")
        if env["ir.default"].search_count(
            [
                ("field_id.ttype", "=", "many2one"),
                ("field_id.relation", "=", "res.partner"),
                ("json_value", "in", [str(id_) for id_ in ids]),
            ]
        ):
            raise RuntimeError("Cleanup blocked by unowned defaults")
        # Reject every external incoming FK row, not only ON DELETE CASCADE.
        # In particular, never SET NULL an unowned child's parent_id.
        env.cr.execute("""
            SELECT n.nspname, t.relname, a.attname, cardinality(c.conkey)
              FROM pg_constraint c
              JOIN pg_class t ON t.oid = c.conrelid
              JOIN pg_namespace n ON n.oid = t.relnamespace
              JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = c.conkey[1]
             WHERE c.contype = 'f' AND c.confrelid = 'res_partner'::regclass
        """)
        for schema, table, column, width in env.cr.fetchall():
            if width != 1:
                raise RuntimeError("Cleanup blocked by unaudited composite FK")
            exclusion = (
                SQL("AND id NOT IN %s", ids) if table == "res_partner" else SQL("")
            )
            env.cr.execute(
                SQL(
                    "SELECT 1 FROM %s WHERE %s IN %s %s LIMIT 1",
                    SQL.identifier(schema, table),
                    SQL.identifier(column),
                    ids,
                    exclusion,
                )
            )
            if env.cr.fetchone():
                raise RuntimeError(f"Cleanup blocked by unowned {table}.{column}")
        # JSONB company-dependent many2ones have no SQL FK, but unlink mutates them.
        for field in self.registry.many2one_company_dependents["res.partner"]:
            env.cr.execute(
                SQL(
                    "SELECT 1 FROM %s WHERE %s @? %s LIMIT 1",
                    SQL.identifier(env[field.model_name]._table),
                    SQL.identifier(field.name),
                    "$.* ? (" + " || ".join(f"@ == {id_}" for id_ in ids) + ")",
                )
            )
            if env.cr.fetchone():
                raise RuntimeError("Cleanup blocked by unowned company-dependent value")

    def close(self):
        def cleanup(env):
            records = env["res.partner"].browse(self.owned["res.partner"]).exists()
            if records:
                records.unlink()

        # Always a fresh cursor; retain IDs after failures for retry/idempotence.
        self.run(cleanup)
