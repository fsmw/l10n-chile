# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from psycopg2.errors import SerializationFailure

from odoo import Command, api, fields, sql_db
from odoo.exceptions import ValidationError
from odoo.tests import new_test_user
from odoo.tests.common import mute_logger

from ..services.common import BINDING, MAPPING, IncomingAccountError
from ..services.evidence_service import IncomingAccountEvidenceService


class CommittedIncomingFixture:
    """Independent, committed synthetic records, removed after each race.

    Registry test cursors share the outer transaction. Raw connections here are
    deliberately independent. Only this test harness commits its own records;
    neither the outer test transaction nor any fiscal workflow is committed.
    """

    def __init__(self, case, content, load_chart=False):
        self.registry = case.env.registry
        self.db = sql_db.db_connect(case.env.cr.dbname)
        with self.db.cursor() as cr:
            cr.transaction = api.Transaction(self.registry)
            cr.execute("SET LOCAL lock_timeout = '10s'")
            env = api.Environment(cr, 1, {"no_reset_password": True}, su=True)
            country, currency = env.ref("base.cl"), env.ref("base.CLP")
            # The outer accounting fixture may have activated CLP without committing.
            # Avoid writing that shared currency row while creating this
            # isolated company.
            env.cache.set(currency, currency._fields["active"], True)
            prefix = "Synthetic concurrency " + uuid4().hex
            native_write = type(env["res.users"]).write

            def isolated_root_membership(users, values):
                # Company creation normally enrolls root. The outer TransactionCase
                # already owns that row; this fixture never changes global membership.
                if (
                    users.ids == [1]
                    and users.env.cr is cr
                    and set(values) == {"company_ids"}
                ):
                    return True
                return native_write(users, values)

            with patch.object(
                type(env["res.users"]), "write", isolated_root_membership
            ):
                company = env["res.company"].create(
                    dict(name=prefix, country_id=country.id, currency_id=currency.id)
                )
            if load_chart:
                # Outside Odoo's test mode, finish its scheduled chart setup before
                # enabling the bridge. The fixture accounts below remain explicit.
                cr.flush()
                cr.precommit.run()
            company.write(
                dict(
                    vat="76234567-6",
                    account_fiscal_country_id=country.id,
                    dte_sii_environment="demo",
                )
            )
            self.company_id = company.id
            self.context = dict(allowed_company_ids=[company.id])
            env = api.Environment(cr, 1, self.context, su=True)
            company = env["res.company"].browse(company.id)
            account = env["account.account"].create(
                dict(
                    name=prefix,
                    code="700123",
                    account_type="expense",
                    company_ids=[Command.set([company.id])],
                )
            )
            payable = env["account.account"].create(
                dict(
                    name=prefix + " payable",
                    code="210123",
                    account_type="liability_payable",
                    reconcile=True,
                    company_ids=[Command.set([company.id])],
                )
            )
            supplier = env["res.partner"].create(
                dict(
                    name=prefix + " supplier",
                    vat="76864280-K",
                    country_id=country.id,
                    company_id=company.id,
                    l10n_cl_sii_taxpayer_type="1",
                    property_account_payable_id=payable.id,
                )
            )
            journal = env["account.journal"].create(
                dict(
                    name=prefix,
                    code="IC01",
                    type="purchase",
                    company_id=company.id,
                    l10n_latam_use_documents=True,
                )
            )
            group = env["account.tax.group"].create(
                dict(name=prefix, company_id=company.id, country_id=country.id)
            )
            tax = env["account.tax"].create(
                dict(
                    name=prefix,
                    company_id=company.id,
                    amount=19,
                    amount_type="percent",
                    type_tax_use="purchase",
                    l10n_cl_sii_code=14,
                    tax_group_id=group.id,
                    price_include_override="tax_excluded",
                )
            )
            exempt = tax.copy(
                dict(name=prefix + " exempt", amount=0, l10n_cl_sii_code=0)
            )
            company.write(
                dict(
                    dte_inbox_account_journal_id=journal.id,
                    dte_inbox_account_vat_tax_id=tax.id,
                    dte_inbox_account_exempt_tax_id=exempt.id,
                    dte_inbox_account_enabled=True,
                )
            )
            native_get_param = type(env["ir.config_parameter"]).get_param

            def isolated_digest(parameters, key, default=False):
                if key == "digest.default_digest_emails" and parameters.env.cr is cr:
                    return False
                return native_get_param(parameters, key, default)

            # User creation must not subscribe to a global digest locked
            # by the outer test.
            with patch.object(
                type(env["ir.config_parameter"]), "get_param", isolated_digest
            ):
                user = new_test_user(
                    env,
                    login=uuid4().hex,
                    groups=(
                        "base.group_user,l10n_cl_dte_inbox_account.group_inbox_account_manager,l10n_cl_dte_core.group_dte_manager"
                    ),
                    company_id=company.id,
                    company_ids=[Command.set([company.id])],
                    context={"no_reset_password": True},
                )
            self.uid = user.id
            self.partner_ids = (supplier | company.partner_id | user.partner_id).ids
            env = api.Environment(cr, user.id, self.context)
            incoming = env["l10n_cl.incoming.dte"]._import_xml(content)
            incoming.action_mark_reviewed()
            document = IncomingAccountEvidenceService(env).read(incoming)
            env[MAPPING].create(
                dict(
                    name=prefix,
                    company_id=company.id,
                    partner_id=supplier.id,
                    match_kind="default",
                    source_uom=document["lines"][0]["unit"],
                    uom_id=env.ref("uom.product_uom_unit").id,
                    account_id=account.id,
                    tax_id=tax.id,
                )
            )
            self.source_id, self.supplier_id = incoming.id, supplier.id
            self.company_partner_id, self.folio = company.partner_id.id, incoming.folio
            self.wizard_ids = []
            for _ in range(2):
                action = incoming.action_prepare_vendor_bill()
                wizard = env["l10n_cl.incoming.dte.bill.wizard"].browse(
                    action["res_id"]
                )
                case.assertFalse(wizard.has_blockers, wizard.diagnostic_text)
                self.wizard_ids.append(wizard.id)
            self.manual_values = dict(
                move_type="in_invoice",
                company_id=company.id,
                partner_id=supplier.id,
                journal_id=journal.id,
                currency_id=currency.id,
                invoice_date=incoming.issue_date,
                date=fields.Date.today(),
                l10n_latam_document_type_id=env.ref("l10n_cl.dc_a_f_dte").id,
                l10n_latam_document_number=str(incoming.folio),
                invoice_line_ids=[
                    Command.create(
                        dict(
                            name="Synthetic detail",
                            account_id=account.id,
                            quantity=1,
                            price_unit=1000,
                            tax_ids=[Command.set([tax.id])],
                        )
                    )
                ],
            )
            # Transaction boundary belongs to this independent test fixture.
            cr.commit()  # pylint: disable=invalid-commit

    def run(self, callback):
        with self.db.cursor() as cr:
            cr.transaction = api.Transaction(self.registry)
            env = api.Environment(cr, self.uid, self.context)
            # CLP is active in the outer fixture, whose uncommitted currency row
            # must not be touched by these independent transaction tests.
            currency = env.ref("base.CLP")
            env.cache.set(currency, currency._fields["active"], True)
            result = callback(env)
            cr.commit()  # pylint: disable=invalid-commit
            return result

    def race(self, left, right):
        barrier = Barrier(2)

        def worker(callback):
            for attempt in range(4):
                with self.db.cursor() as cr:
                    cr.transaction = api.Transaction(self.registry)
                    env = api.Environment(cr, self.uid, self.context)
                    # CLP is active in the outer fixture, whose uncommitted currency row
                    # must not be touched by these independent transaction tests.
                    currency = env.ref("base.CLP")
                    env.cache.set(currency, currency._fields["active"], True)
                    try:
                        if not attempt:
                            cr.execute(
                                "SELECT id FROM res_partner WHERE id = %s",
                                (self.supplier_id,),
                            )
                            barrier.wait(timeout=20)
                        result = callback(env)
                        cr.commit()  # pylint: disable=invalid-commit
                        return dict(status="ok", result=result, retries=attempt)
                    except SerializationFailure:
                        cr.rollback()
                    except IncomingAccountError as error:
                        cr.rollback()
                        return dict(status=error.diagnostic["code"], retries=attempt)
                    except ValidationError:
                        cr.rollback()
                        return dict(status="validation_error", retries=attempt)
            raise AssertionError("The transaction did not stabilize after retries")

        with mute_logger("odoo.sql_db"), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker, callback) for callback in (left, right)]
            return [future.result(timeout=45) for future in futures]

    def counts(self):
        def inspect(env):
            moves = env["account.move"].search(
                [("company_id", "=", self.company_id), ("move_type", "=", "in_invoice")]
            )
            matching = moves.filtered(
                lambda move: move.l10n_latam_document_number == str(self.folio)
            )
            return len(matching), env[BINDING].search_count(
                [("company_id", "=", self.company_id)]
            )

        return self.run(inspect)

    def close(self):
        with self.db.cursor() as cr:
            cr.transaction = api.Transaction(self.registry)
            env = api.Environment(cr, 1, self.context, su=True)
            company = env["res.company"].browse(self.company_id)
            company.dte_inbox_account_enabled = False
            # These statements delete only this committed synthetic fixture.
            # Production provenance intentionally has no deletion bypass.
            cr.execute(
                (
                    "DELETE FROM l10n_cl_incoming_dte_account_binding WHERE "
                    "company_id = %s"
                ),
                (self.company_id,),
            )
            cr.execute(
                (
                    "DELETE FROM l10n_cl_incoming_dte_event WHERE "
                    "incoming_document_id IN (SELECT id FROM l10n_cl_incoming_dte "
                    "WHERE company_id = %s)"
                ),
                (self.company_id,),
            )
            cr.execute(
                "DELETE FROM l10n_cl_incoming_dte WHERE company_id = %s",
                (self.company_id,),
            )
            cr.execute(
                "DELETE FROM account_move WHERE company_id = %s", (self.company_id,)
            )
            env.invalidate_all()
            env[MAPPING].search([("company_id", "=", self.company_id)]).unlink()
            env["account.tax"].search([("company_id", "=", self.company_id)]).unlink()
            env["account.journal"].search(
                [("company_id", "=", self.company_id)]
            ).unlink()
            env["res.partner"].browse(self.partner_ids).write(
                {
                    "property_account_payable_id": False,
                    "property_account_receivable_id": False,
                }
            )
            env["account.account"].search(
                [("company_ids", "in", [self.company_id])]
            ).unlink()
            env["account.tax.group"].search(
                [("company_id", "=", self.company_id)]
            ).unlink()
            env["res.users"].browse(self.uid).unlink()
            company.unlink()
            env["res.partner"].browse(self.partner_ids).exists().unlink()
            cr.commit()  # pylint: disable=invalid-commit
