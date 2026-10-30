# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import base64
import os
from datetime import date
from unittest.mock import patch

from odoo import api
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from ..services.identity import DteIdentity
from ..services.recovery_service import RecoveryResult
from ..services.xml_dte import extract_candidates


@tagged("post_install", "-at_install")
class TestDteRecoveryPersistence(AccountTestInvoicingCommon):
    """The recovery path must attach evidence, never generate accounting."""

    @classmethod
    @AccountTestInvoicingCommon.setup_country("cl")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.company.partner_id.vat = "76543210-3"
        cls.sale_journal = cls.company_data["default_journal_sale"]
        cls.partner_sii = cls.env["res.partner"].create(
            {
                "name": "SII customer",
                "vat": "76086428-5",
                "country_id": cls.env.ref("base.cl").id,
                "l10n_latam_identification_type_id": cls.env.ref("l10n_cl.it_RUT").id,
                "l10n_cl_sii_taxpayer_type": "1",
            }
        )
        cls.has_dte_fields = "l10n_cl_dte_status" in cls.env["account.move"]._fields

    def _add_recovery_group(self):
        self.env.user.write(
            {
                "groups_id": [
                    (
                        4,
                        self.env.ref(
                            "l10n_cl_dte_mipyme_recovery.group_mipyme_recovery_user"
                        ).id,
                    )
                ],
            }
        )

    def test_wizard_rejects_non_positive_folio(self):
        self._add_recovery_group()
        self.company.partner_id.write({"vat": "765432103"})
        wizard = self.env["l10n_cl.dte.recovery.wizard"].create(
            {
                "company_id": self.company.id,
                "dte_type": "33",
                "folio": 0,
                "date_from": date(2026, 8, 1),
                "date_to": date(2026, 8, 31),
            }
        )
        with self.assertRaises(ValidationError):
            wizard.action_recover()

    def test_recipient_rut_matching_ignores_formatting(self):
        recovery_model = self.env["l10n_cl.dte.recovery"]
        self.assertTrue(recovery_model._same_rut("96.961.400-6", "96961400-6"))
        self.assertFalse(recovery_model._same_rut("96.961.400-6", "96500000-0"))

    def test_identity_lock_serializes_concurrent_transactions(self):
        first = self.env.registry.cursor()
        second = self.env.registry.cursor()
        try:
            api.Environment(first, self.env.uid, {})[
                "l10n_cl.dte.recovery"
            ]._lock_identity(12345, "33", 876543)
            first.execute(
                "SELECT classid::bigint, objid::bigint FROM pg_locks "
                "WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
            )
            classid, objid = first.fetchone()
            keys = tuple(
                value - 2**32 if value >= 2**31 else value for value in (classid, objid)
            )
            second.execute("SELECT pg_try_advisory_xact_lock(%s, %s)", keys)
            self.assertFalse(second.fetchone()[0])
            first.commit()
            second.execute("SELECT pg_try_advisory_xact_lock(%s, %s)", keys)
            self.assertTrue(second.fetchone()[0])
        finally:
            first.close()
            second.close()

    def test_imported_invoice_stays_draft_then_posts_without_edi(self):
        document_type = self.env["l10n_latam.document.type"].search(
            [("code", "=", 34), ("country_id.code", "=", "CL")], limit=1
        )
        plan = self.env["account.analytic.plan"].search([], limit=1)
        if not plan:
            plan = self.env["account.analytic.plan"].create(
                {"name": "Recovered DTE projects"}
            )
        analytic = self.env["account.analytic.account"].create(
            {
                "name": "Recovered DTE project",
                "company_id": self.company.id,
                "plan_id": plan.id,
            }
        )
        values = {
            "partner_id": self.partner_sii.id,
            "move_type": "out_invoice",
            "invoice_date": "2026-09-04",
            "journal_id": self.sale_journal.id,
            "l10n_latam_document_type_id": document_type.id,
            "l10n_latam_document_number": "224",
            "company_id": self.company.id,
            "l10n_cl_mipyme_imported": True,
            "l10n_cl_mipyme_xml_total": 100,
            "invoice_line_ids": [
                (
                    0,
                    0,
                    {
                        "name": "Recovered exempt service",
                        "account_id": self.sale_journal.default_account_id.id,
                        "quantity": 1,
                        "price_unit": 100,
                    },
                )
            ],
        }
        if self.has_dte_fields:
            values["l10n_cl_dte_status"] = "manual"
        invoice = self.env["account.move"].create(values)
        self.assertEqual(invoice.state, "draft")
        invoice.invoice_line_ids.analytic_distribution = {str(analytic.id): 100}
        if self.has_dte_fields:
            with patch.object(
                type(invoice), "l10n_cl_send_dte_to_sii", create=True
            ) as send_dte:
                invoice.action_post()
            send_dte.assert_not_called()
            self.assertEqual(invoice.l10n_cl_dte_status, "accepted")
        else:
            invoice.action_post()
        self.assertEqual(invoice.state, "posted")
        with self.assertRaises(UserError):
            invoice.write({"partner_id": self.company.partner_id.id})

    def test_recovery_associates_one_existing_invoice_idempotently(self):
        self._add_recovery_group()
        document_type = self.env.ref("l10n_cl.dc_a_f_dte")
        invoice = self.env["account.move"].create(
            {
                "partner_id": self.partner_sii.id,
                "move_type": "out_invoice",
                "invoice_date": "2026-08-20",
                "journal_id": self.sale_journal.id,
                "l10n_latam_document_type_id": document_type.id,
                "l10n_latam_document_number": "42",
                "company_id": self.company.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Recovery integration test",
                            "product_id": self.product_a.id,
                            "product_uom_id": self.product_a.uom_id.id,
                            "quantity": 1,
                            "price_unit": 100,
                        },
                    )
                ],
            }
        )
        amount_before = invoice.amount_total
        with open(
            os.path.join(os.path.dirname(__file__), "fixtures", "mipyme_set.xml"), "rb"
        ) as fixture:
            payload = fixture.read()
        candidate = next(
            item for item in extract_candidates(payload) if item.identity.folio == 42
        )
        result = RecoveryResult(
            "recovered",
            DteIdentity("76543210-3", "33", 42),
            candidate=candidate,
            digest=candidate.digest,
        )

        recovery_model = self.env["l10n_cl.dte.recovery"]
        self.assertEqual(invoice.l10n_latam_document_type_id.code, "33")
        self.assertEqual(
            recovery_model._matching_moves(self.company, result.identity), invoice
        )
        recovery = recovery_model.persist_result(
            result, self.company, date(2026, 8, 1), date(2026, 8, 31)
        )
        self.assertEqual(recovery.status, "recovered")
        self.assertEqual(recovery.move_id, invoice)
        self.assertEqual(invoice.state, "draft")
        self.assertEqual(invoice.amount_total, amount_before)
        if self.has_dte_fields:
            self.assertEqual(invoice.l10n_cl_dte_file, recovery.attachment_id)
        else:
            self.assertEqual(recovery.attachment_id.res_model, "account.move")
            self.assertEqual(recovery.attachment_id.res_id, invoice.id)
        self.assertEqual(
            base64.b64decode(recovery.attachment_id.datas), candidate.raw_xml
        )

        rerun = recovery_model.persist_result(
            result, self.company, date(2026, 8, 1), date(2026, 8, 31)
        )
        self.assertEqual(rerun, recovery)
        self.assertEqual(
            recovery_model.search_count([("digest", "=", candidate.digest)]), 1
        )
