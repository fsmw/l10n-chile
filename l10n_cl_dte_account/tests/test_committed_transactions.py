# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import importlib
import importlib.util
from contextlib import closing, contextmanager
from unittest import TestCase
from unittest.mock import patch

from odoo import Command, api, models, sql_db
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

PARTNER = "res.partner"


def fail():
    raise RuntimeError("sentinel")


@tagged("post_install", "-at_install")
class TestCommittedTransactions(TransactionCase):
    def fixture(self):
        module = __package__ + ".common_committed_transactions"
        self.assertIsNotNone(importlib.util.find_spec(module), "Fixture missing")
        fixture = importlib.import_module(module).CommittedTransactions(self)
        self.addCleanup(fixture.close)
        return fixture

    def raises(self, message="sentinel", error=RuntimeError):
        # Unlike TransactionCase.assertRaises, do not wrap the outer test cursor.
        return TestCase.assertRaisesRegex(self, error, message)

    def make(self, fixture, **values):
        return fixture.run(
            lambda e: e[PARTNER].create({"name": "Synthetic", **values}).id
        )

    def alive(self, fixture, ids, model=PARTNER):
        return fixture.run(lambda e: e[model].browse(ids).exists().ids)

    def write_partner(self, fixture, ids, **values):
        return fixture.run(lambda e: e[PARTNER].browse(ids).write(values))

    def assert_rejected_mutation(self, fixture, mutation):
        def callback(env):
            mutation(env)
            fail()  # Roll back even when the missing guard permits the mutation.

        with self.raises("unowned"):
            fixture.run(callback)

    @contextmanager
    def dependency(self, fixture, model, values, created, commit_fault=False):
        # Arm exact-ID cleanup before creation/commit, including postcommit failures.
        # Explicit policy for the root, followers, scheduler and bus side effects.
        order = (model, "mail.followers", "ir.cron.trigger", "bus.bus")
        try:
            with fixture.cursor() as cr:
                env = api.Environment(cr, 1, {"tracking_disable": True}, su=True)
                original = models.BaseModel._create

                def observe(records, data):
                    result = original(records, data)
                    if records.env.cr is cr and records.env.registry is self.registry:
                        self.assertIn(result._name, order)
                        created.setdefault(result._name, set()).update(result.ids)
                    return result

                with patch.object(models.BaseModel, "_create", observe):
                    env[model].create(values)
                    if model == "mail.activity":
                        env["mail.followers"].browse(
                            created.get("mail.followers", ())
                        ).unlink()
                    if commit_fault:
                        cr.postcommit.add(fail)
                    # This cursor belongs exclusively to the negative-test fixture.
                    cr.commit()  # pylint: disable=invalid-commit
            yield
        finally:
            with fixture.cursor() as cr:
                env = api.Environment(cr, 1, {"active_test": False}, su=True)
                with patch.object(models.BaseModel, "_create", observe):
                    for name in order:
                        env[name].browse(created.get(name, ())).exists().unlink()
                        cr.flush()
                # Fresh observer; only IDs captured during this exact creation.
                cr.commit()  # pylint: disable=invalid-commit

    def test_graph_manifest_visibility_and_single_commit(self):
        fixture, foreign = self.fixture(), []
        marker = self.env[PARTNER].create({"name": "Outer-only"})
        commits, original = [], sql_db.Cursor.commit

        def commit(cr):
            commits.append(cr)
            return original(cr)

        def create(env):
            with fixture.cursor() as cr:
                other = api.Environment(cr, 1, env.context, su=True)
                foreign.extend(other[PARTNER].create({"name": "Foreign"}).ids)
                other.flush_all()
            parent = (
                env[PARTNER]
                .with_context(default_lang=False)
                .create(
                    {
                        "name": "Synthetic",
                        "lang": "en_US",
                        "child_ids": [
                            Command.create({"name": "Child", "street": "Owned address"})
                        ],
                    }
                )
            )
            child = (
                env[PARTNER]
                .with_context(default_lang=False)
                .create({"name": "Inherited language", "parent_id": parent.id})
            )
            self.assertEqual(child.lang, parent.lang)
            return [parent.id, *parent.child_ids.ids]

        with patch.object(sql_db.Cursor, "commit", commit):
            manifest = fixture.run(create)
        self.assertEqual(len(commits), 1)
        self.assertEqual(len(manifest), 3)
        snapshot = fixture.owned
        self.assertEqual(snapshot, {PARTNER: frozenset(manifest)})
        self.assertFalse(self.alive(fixture, foreign))
        with self.raises("", TypeError):
            snapshot[PARTNER] = frozenset()
        with fixture.cursor() as left, fixture.cursor() as right:
            left.execute("SELECT pg_backend_pid()")
            right.execute("SELECT pg_backend_pid()")
            self.assertNotEqual(left.fetchone(), right.fetchone())
            for cr in (left, right):
                env = api.Environment(cr, 1, {}, su=True)
                self.assertFalse(env[PARTNER].browse(marker.id).exists())
                self.assertEqual(env[PARTNER].browse(manifest).exists().ids, manifest)
            env[PARTNER].browse(manifest[0]).name = "Uncommitted"
            env.flush_all()
        self.assertEqual(
            fixture.run(lambda e: e[PARTNER].browse(manifest[0]).name), "Synthetic"
        )
        self.write_partner(fixture, manifest, active=False, company_id=False)
        fixture.close()
        fixture.close()
        self.assertFalse(self.alive(fixture, manifest))
        self.assertEqual(snapshot, fixture.owned)
        self.assertTrue(marker.exists())

    def test_nonplain_returns_and_callback_error(self):
        fixture, created = self.fixture(), []
        for kind in ("error", "record", "env", "cursor", "object", "cycle", "key"):

            def callback(env, kind=kind):
                record = env[PARTNER].create({"name": "Rejected"})
                created.extend(record.ids)
                if kind == "error":
                    fail()
                if kind == "key":
                    return {record: "invalid key"}
                cyclic = []
                cyclic.append(cyclic)
                values = dict(
                    record=record, env=env, cursor=env.cr, object=object(), cycle=cyclic
                )
                return {"nested": [values[kind]]}

            with (
                self.subTest(kind=kind),
                self.raises("", RuntimeError if kind == "error" else TypeError),
            ):
                fixture.run(callback)
            self.assertFalse(self.alive(fixture, created))
        plain = [None, False, 1, 1.5, "text", (2,)]
        self.assertEqual(fixture.run(lambda e: plain), plain)

    def test_controls_and_caught_explicit_flush_poison(self):
        fixture, created = self.fixture(), []
        for phase in ("callback", "precommit"):
            for control in ("commit", "rollback", "close", "flush"):

                def callback(env, phase=phase, control=control):
                    created.extend(env[PARTNER].create({"name": "Control"}).ids)

                    def attempt():
                        if control == "flush":
                            env.cr.precommit.add(fail)
                        with self.raises("sentinel|cursor control"):
                            getattr(env.cr, control)()

                    if phase == "precommit":
                        env.cr.precommit.add(attempt)
                    else:
                        attempt()

                with closing(fixture):
                    with (
                        self.subTest(phase=phase, control=control),
                        self.raises("poison"),
                    ):
                        fixture.run(callback)
                    with self.subTest(phase=phase, control=control, check="rollback"):
                        self.assertFalse(self.alive(fixture, created))

    def test_full_create_chain_failures_poison(self):
        fixture, inserted = self.fixture(), []
        for kind in ("low", "public", "inverse"):
            owner = type(self.env[PARTNER]) if kind == "public" else models.BaseModel
            method = "create" if kind == "public" else "_create"
            original = getattr(owner, method)

            def broken(records, values, original=original, kind=kind):
                result = original(records, values)
                inserted.extend(result.ids)
                if kind != "inverse":
                    raise ValueError("after create")
                return result

            for phase in ("callback", "precommit"):
                before = len(inserted)

                def callback(env, kind=kind, phase=phase):
                    def attempt():
                        values = {"name": "Rejected creation", "user_id": False}
                        if kind == "inverse":
                            values["company_type"] = "invalid"
                        with self.raises("", ValueError):
                            env[PARTNER].create(values)

                    if phase == "precommit":
                        env.cr.precommit.add(attempt)
                    else:
                        attempt()

                with closing(fixture):
                    with (
                        self.subTest(kind=kind, phase=phase),
                        patch.object(owner, method, broken),
                        self.raises("poison"),
                    ):
                        fixture.run(callback)
                    with self.subTest(
                        kind=kind, phase=phase, check="inserted then rolled back"
                    ):
                        self.assertGreater(len(inserted), before)
                        self.assertFalse(self.alive(fixture, inserted))

    def test_rollback_hooks_cannot_commit_and_release_connections(self):
        fixture, created = self.fixture(), []
        original = models.BaseModel._create
        for hook in ("prerollback", "postrollback"):
            for action in ("commit", "raise"):
                cursors = []

                def callback(env, hook=hook, action=action, remember=cursors.append):
                    remember(env.cr)
                    created.extend(env[PARTNER].create({"name": "Rollback hook"}).ids)
                    getattr(env.cr, hook).add(
                        lambda: env.cr.commit() if action == "commit" else fail()
                    )
                    fail()

                try:
                    with (
                        self.subTest(hook=hook, action=action),
                        patch.object(
                            sql_db.Cursor,
                            "commit",
                            side_effect=AssertionError("Unexpected commit"),
                        ),
                        self.raises("sentinel|cursor control"),
                    ):
                        fixture.run(callback)
                    with self.subTest(hook=hook, action=action, check="release"):
                        cr = cursors[0]
                        self.assertTrue(cr.closed)
                        self.assertIs(models.BaseModel._create, original)
                        connections = cr._Cursor__pool._connections
                        self.assertFalse(
                            any(c is cr._cnx and used for c, used, _ in connections)
                        )
                        self.assertFalse(self.alive(fixture, created))
                finally:
                    # RED-only recovery of these exact cursors if native close aborted.
                    for cr in cursors:
                        if not cr.closed:
                            cr._cnx.close()
                            cr._Cursor__pool.give_back(cr._cnx, keep_in_pool=False)
                            cr._closed = True
                    fixture.close()

    def test_unowned_parent_address_cannot_change(self):
        guard, fixture = self.fixture(), self.fixture()
        parent = self.make(guard, is_company=True)
        try:
            with self.subTest(check="rejected"), self.raises("unowned"):
                self.make(fixture, parent_id=parent, street="Must not propagate")
            self.assertFalse(guard.run(lambda e: e[PARTNER].browse(parent).street))
        finally:
            fixture.close()
            guard.close()

    def test_precommit_capture_result_revalidation_and_unsupported_model(self):
        fixture, created = self.fixture(), []

        def callback(env):
            env.cr.precommit.add(
                lambda: created.extend(env[PARTNER].create({"name": "Precommit"}).ids)
            )

        fixture.run(callback)
        self.assertEqual(fixture.owned[PARTNER], set(created))
        fixture.close()
        self.assertFalse(self.alive(fixture, created))

        def invalid(env):
            result = []
            env.cr.precommit.add(lambda: result.append(env))
            return result

        with self.raises("", TypeError):
            fixture.run(invalid)

        def unsupported(env):
            with self.raises("unsupported"):
                env["res.partner.category"].create({"name": "Unsupported"})

        with self.raises("poison"):
            fixture.run(unsupported)

    def test_postcommit_failure_retains_ledger_for_fresh_cleanup(self):
        fixture, created, cursors = self.fixture(), [], []
        for sql in (False, True):

            def callback(env, sql=sql):
                cursors.append(env.cr)
                created.extend(env[PARTNER].create({"name": "Committed"}).ids)
                env.cr.postcommit.add(
                    lambda: env.cr.execute("SELECT 1") if sql else fail()
                )

            with self.raises("sentinel|Postcommit SQL"):
                fixture.run(callback)
            self.assertTrue(cursors[-1].closed)
            self.assertEqual(fixture.owned[PARTNER], set(created))
            self.assertTrue(self.alive(fixture, created))
            fixture.close()
            fixture.close()
            self.assertFalse(self.alive(fixture, created))

    def test_cleanup_failure_retry_and_unowned_child(self):
        fixture = self.fixture()
        parent = self.make(fixture, company_id=self.env.company.id)
        created = {}
        with self.dependency(
            fixture, PARTNER, {"name": "Foreign child", "parent_id": parent}, created
        ):
            with self.raises("unowned res_partner"):
                fixture.close()
            child = next(iter(created[PARTNER]))
            for target in (parent, child):
                with self.subTest(unlink_target=target):
                    self.assert_rejected_mutation(
                        fixture,
                        lambda e, target=target: e[PARTNER].browse(target).unlink(),
                    )
            self.assert_rejected_mutation(
                fixture,
                lambda e: (
                    e[PARTNER]
                    .browse(parent)
                    .write({"child_ids": [Command.delete(child)]})
                ),
            )
            self.assert_rejected_mutation(
                fixture, lambda e: e[PARTNER].browse(child).write({"name": "Unowned"})
            )
            self.assertEqual(
                fixture.run(lambda e: e[PARTNER].browse(child).parent_id.id), parent
            )
        self.write_partner(fixture, [parent], active=False, company_id=False)
        owner, snapshot = type(self.env[PARTNER]), fixture.owned
        original = owner.unlink

        def broken(records):
            original(records)
            fail()

        with patch.object(owner, "unlink", broken), self.raises():
            fixture.close()
        self.assertEqual(self.alive(fixture, [parent]), [parent])
        self.assertEqual(fixture.owned, snapshot)
        fixture.close()
        self.assertFalse(self.alive(fixture, [parent]))

    def test_exact_dependency_guards_and_commit_fault_cleanup(self):
        guard, fixture = self.fixture(), self.fixture()
        foreign, parent = self.make(guard), self.make(fixture)
        target, document = (
            {"res_model": PARTNER, "res_id": parent},
            {"model": PARTNER, "res_id": parent},
        )
        cases = [
            ("ir.attachment", dict(target, name="Unowned")),
            ("ir.attachment", dict(target, name="Hidden", res_field="image_1920")),
            (
                "ir.model.data",
                dict(document, module="n03c_test", name="unowned"),
            ),
            ("mail.message", dict(document, body="Unowned")),
            (
                "mail.followers",
                dict(target, partner_id=self.env.user.partner_id.id),
            ),
            ("mail.followers", dict(target, res_id=foreign, partner_id=parent)),
            (
                "mail.scheduled.message",
                dict(
                    document,
                    author_id=self.env.user.partner_id.id,
                    scheduled_date="2099-01-01",
                ),
            ),
            (
                "mail.activity",
                {
                    "res_model_id": self.env["ir.model"]._get_id(PARTNER),
                    "res_id": parent,
                    "date_deadline": "2099-01-01",
                },
            ),
            (
                "ir.default",
                {
                    "field_id": self.env["ir.model.fields"]
                    ._get(PARTNER, "parent_id")
                    .id,
                    "json_value": str(parent),
                },
            ),
        ]
        snapshot = fixture.owned
        for model, values in cases:
            expected = "defaults" if model == "ir.default" else model
            if model == "mail.followers" and values["partner_id"] == parent:
                expected = "mail_followers.partner_id"
            created = {}
            with (
                self.subTest(model=model, expected=expected),
                self.dependency(fixture, model, values, created),
            ):
                with (
                    patch.object(
                        type(self.env[PARTNER]),
                        "unlink",
                        side_effect=AssertionError("Unsafe unlink"),
                    ),
                    self.raises("unowned " + expected),
                ):
                    fixture.close()
                self.assertEqual(fixture.owned, snapshot)
                self.assertTrue(self.alive(fixture, created[model], model))
                if model == "mail.activity":
                    self.assert_rejected_mutation(
                        fixture,
                        lambda e: e[PARTNER].browse(parent).write({"active": False}),
                    )
                    self.assertTrue(self.alive(fixture, created[model], model))
        created = {}
        fault = self.dependency(
            fixture, "ir.attachment", dict(target, name="Commit fault"), created, True
        )
        with self.raises(), fault:
            self.fail("Postcommit fault must escape")
        self.assertTrue(created["ir.attachment"])
        self.assertFalse(self.alive(fixture, created["ir.attachment"], "ir.attachment"))
        fixture.close()
        self.assertFalse(self.alive(fixture, [parent]))
