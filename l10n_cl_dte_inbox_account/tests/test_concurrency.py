# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo.tests import tagged

from ..services.binding_service import IncomingAccountBindingService
from .common import IncomingAccountCase
from .concurrency import CommittedIncomingFixture


@tagged("post_install", "-at_install")
class TestIncomingAccountConcurrency(IncomingAccountCase):
    def fixture(self):
        fixture = CommittedIncomingFixture(self, self.signed())
        self.addCleanup(fixture.close)
        return fixture

    def confirm(self, env, wizard_id):
        return (
            IncomingAccountBindingService(env)
            .confirm(env["l10n_cl.incoming.dte.bill.wizard"].browse(wizard_id))
            .id
        )

    def test_two_confirmations_same_source(self):
        fixture = self.fixture()
        left, right = fixture.wizard_ids
        results = fixture.race(
            lambda env: self.confirm(env, left), lambda env: self.confirm(env, right)
        )
        self.assertEqual([result["status"] for result in results], ["ok", "ok"])
        self.assertEqual(results[0]["result"], results[1]["result"])
        self.assertEqual(fixture.counts(), (1, 1))

    def test_two_sources_same_fiscal_identity(self):
        fixture = self.fixture()

        def add_other_environment(env):
            company = env.company
            source = env["l10n_cl.incoming.dte"].browse(fixture.source_id)
            company.sudo().dte_sii_environment = "certification"
            other = env["l10n_cl.incoming.dte"]._import_xml(
                base64.b64decode(source.sudo().raw_xml)
            )
            other.action_mark_reviewed()
            company.sudo().dte_sii_environment = "demo"
            env["l10n_cl.incoming.dte.bill.wizard"].browse(
                fixture.wizard_ids[0]
            ).action_refresh_preview()
            return other.action_prepare_vendor_bill()["res_id"]

        other = fixture.run(add_other_environment)
        results = fixture.race(
            lambda env: self.confirm(env, fixture.wizard_ids[0]),
            lambda env: self.confirm(env, other),
        )
        self.assertEqual(
            sorted(result["status"] for result in results),
            ["environment_mismatch", "ok"],
        )
        self.assertEqual(fixture.counts(), (1, 1))

    def test_manual_create_against_confirm(self):
        fixture = self.fixture()
        results = fixture.race(
            lambda env: self.confirm(env, fixture.wizard_ids[0]),
            lambda env: env["account.move"].create(fixture.manual_values).id,
        )
        self.assertEqual(
            sorted(result["status"] for result in results), ["duplicate_bill", "ok"]
        )
        self.assertEqual(fixture.counts()[0], 1)

    def test_manual_identity_change_and_post(self):
        fixture = self.fixture()
        manual = fixture.run(
            lambda env: (
                env["account.move"]
                .create(
                    dict(
                        fixture.manual_values,
                        l10n_latam_document_number=str(fixture.folio + 1),
                    )
                )
                .id
            )
        )

        def change_and_post(env):
            move = env["account.move"].browse(manual)
            move.l10n_latam_document_number = str(fixture.folio)
            move.action_post()
            return move.id

        results = fixture.race(
            lambda env: self.confirm(env, fixture.wizard_ids[0]), change_and_post
        )
        self.assertEqual(
            sorted(result["status"] for result in results), ["duplicate_bill", "ok"]
        )
        self.assertEqual(fixture.counts()[0], 1)

    def test_lock_order_and_retry(self):
        fixture = self.fixture()

        def lock(env, reverse=False):
            partners = env["res.partner"].browse(
                [fixture.supplier_id, fixture.company_partner_id]
            )
            IncomingAccountBindingService(env)._lock_suppliers(
                partners[::-1] if reverse else partners
            )
            return True

        results = fixture.race(lambda env: lock(env), lambda env: lock(env, True))
        self.assertEqual([result["status"] for result in results], ["ok", "ok"])
        self.assertGreaterEqual(sum(result["retries"] for result in results), 1)
