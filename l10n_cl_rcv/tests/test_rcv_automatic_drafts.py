# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from threading import Event, Thread
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import ValidationError

from .common import RcvTestCommon


class TestRcvDailyScheduler(RcvTestCommon):
    def _make_sync_with_registro_documents(self, company, year=2026, month=9):
        period_model = self.env["l10n.cl.rcv.period"]
        period = period_model.search(
            [
                ("company_id", "=", company.id),
                ("year", "=", year),
                ("month", "=", month),
                ("operation", "=", "purchase"),
            ],
            limit=1,
        )
        if not period:
            period = period_model.create(
                {
                    "company_id": company.id,
                    "year": year,
                    "month": month,
                    "operation": "purchase",
                }
            )
        sync = self.env["l10n.cl.rcv.sync"].create(
            {
                "period_id": period.id,
                "company_id": company.id,
                "operation": "purchase",
                "requested_by": self.env.user.id,
                "source": "sii",
                "state": "success",
            }
        )
        self.env["l10n.cl.rcv.section"].create(
            {"sync_id": sync.id, "sii_state": "Registro", "state": "complete"}
        )
        return sync

    def test_backfill_retries_review_document_with_complete_registro(self):
        company = self.env.company
        company.write(
            {
                "l10n_cl_rcv_service_user_id": self.env.user.id,
                "l10n_cl_rcv_automation_enabled": True,
            }
        )
        sync = self._make_sync_with_registro_documents(company)
        document = self.env["l10n.cl.rcv.document"].create(
            {
                "company_id": company.id,
                "operation": "purchase",
                "source": "sii",
                "tax_period": "202609",
                "document_type_code": "33",
                "issuer_vat": "76123456-0",
                "folio": "98100",
                "sii_state": "Registro",
                "reconciliation_state": "review",
                "last_sync_id": sync.id,
            }
        )
        service = self.env["l10n.cl.rcv.auto.draft"]

        with patch.object(type(service), "_create_document_draft") as create_draft:
            service.process_existing_purchases(company, [(2026, 9)])

        create_draft.assert_called_once_with(document)

    def test_backfill_skips_disabled_incomplete_and_out_of_window_documents(self):
        company = self.env.company
        sync = self._make_sync_with_registro_documents(company, month=8)
        document = self.env["l10n.cl.rcv.document"].create(
            {
                "company_id": company.id,
                "operation": "purchase",
                "source": "sii",
                "tax_period": "202608",
                "document_type_code": "33",
                "issuer_vat": "76123456-0",
                "folio": "98103",
                "sii_state": "Registro",
                "last_sync_id": sync.id,
            }
        )
        service = self.env["l10n.cl.rcv.auto.draft"]

        with patch.object(type(service), "_create_document_draft") as create_draft:
            service.process_existing_purchases(company, [(2026, 8)])
            self.assertFalse(create_draft.called)

            company.write(
                {
                    "l10n_cl_rcv_service_user_id": self.env.user.id,
                    "l10n_cl_rcv_automation_enabled": True,
                }
            )
            sync.section_ids.write({"state": "partial"})
            service.process_existing_purchases(company, [(2026, 8)])

            filter_sync = self._make_sync_with_registro_documents(company, month=8)
            self.env["l10n.cl.rcv.document"].create(
                [
                    {
                        "company_id": company.id,
                        "operation": operation,
                        "source": source,
                        "tax_period": "202608",
                        "document_type_code": "33",
                        "issuer_vat": "76123456-0",
                        "folio": folio,
                        "sii_state": sii_state,
                        "last_sync_id": filter_sync.id,
                    }
                    for folio, operation, source, sii_state in (
                        ("98105", "purchase", "sii", "Pendiente"),
                        ("98106", "sale", "sii", "Registro"),
                        ("98107", "purchase", "csv", "Registro"),
                    )
                ]
            )
            service.process_existing_purchases(company, [(2026, 8)])

            old_sync = self._make_sync_with_registro_documents(company, month=7)
            old_document = document.copy(
                {"tax_period": "202607", "folio": "98104", "last_sync_id": old_sync.id}
            )
            service.process_existing_purchases(company, [(2026, 8)])

        self.assertFalse(create_draft.called)
        self.assertFalse(old_document.automation_error)

    def test_automation_is_disabled_by_default_and_requires_user(self):
        company = self.env.company
        self.assertFalse(company.l10n_cl_rcv_automation_enabled)
        with self.assertRaises(ValidationError):
            company.write({"l10n_cl_rcv_automation_enabled": True})

    def test_daily_scheduler_enqueues_current_and_previous_period_once(self):
        company = self.env.company
        service_user = self.env["res.users"].create(
            {
                "name": "RCV automation test",
                "login": "rcv-automation-test",
                "company_id": company.id,
                "company_ids": [(6, 0, company.ids)],
            }
        )
        company.write(
            {
                "l10n_cl_rcv_service_user_id": service_user.id,
                "l10n_cl_rcv_automation_enabled": True,
            }
        )

        period_model = self.env["l10n.cl.rcv.period"].sudo()
        period_model._cron_schedule_daily_purchases()
        period_model._cron_schedule_daily_purchases()

        today = fields.Date.context_today(
            self.env["l10n.cl.rcv.period"].with_context(tz="America/Santiago")
        )
        if today.month == 1:
            expected_periods = [(today.year, 1), (today.year - 1, 12)]
        else:
            expected_periods = [
                (today.year, today.month),
                (today.year, today.month - 1),
            ]
        domains = [
            ("company_id", "=", company.id),
            ("operation", "=", "purchase"),
            ("scheduled_for", "=", today),
        ]
        syncs = self.env["l10n.cl.rcv.sync"].sudo().search(domains)
        self.assertEqual(len(syncs), 2)
        self.assertEqual(set(syncs.mapped("requested_by").ids), {service_user.id})
        self.assertEqual(set(syncs.mapped("state")), {"queued"})
        self.assertEqual(
            {(sync.period_id.year, sync.period_id.month) for sync in syncs},
            set(expected_periods),
        )
        self.assertEqual(set(syncs.mapped("source")), {"sii"})

    def test_scheduler_ignores_disabled_companies(self):
        self.env["l10n.cl.rcv.period"].sudo()._cron_schedule_daily_purchases()
        scheduled = (
            self.env["l10n.cl.rcv.sync"].sudo().search([("scheduled_for", "!=", False)])
        )
        self.assertFalse(scheduled)

    def test_only_complete_registro_sections_are_sent_for_draft_creation(self):
        period = self.env["l10n.cl.rcv.period"].create(
            {
                "company_id": self.env.company.id,
                "year": 2026,
                "month": 9,
                "operation": "purchase",
            }
        )
        sync = self.env["l10n.cl.rcv.sync"].create(
            {
                "period_id": period.id,
                "company_id": self.env.company.id,
                "operation": "purchase",
                "requested_by": self.env.user.id,
                "source": "sii",
                "state": "success",
            }
        )
        self.env["l10n.cl.rcv.section"].create(
            {"sync_id": sync.id, "sii_state": "REGISTRO", "state": "complete"}
        )
        document = self.env["l10n.cl.rcv.document"].create(
            {
                "company_id": self.env.company.id,
                "operation": "purchase",
                "source": "sii",
                "tax_period": "202609",
                "document_type_code": "33",
                "issuer_vat": "76123456-0",
                "folio": "98001",
                "sii_state": "Registro",
                "last_sync_id": sync.id,
            }
        )

        service = self.env["l10n.cl.rcv.auto.draft"]
        with patch.object(type(service), "_create_document_draft") as create_draft:
            service.process_sync(sync)
        create_draft.assert_called_once()
        self.assertEqual(create_draft.call_args.args[-1], document)

        sync.section_ids.write({"state": "partial"})
        with patch.object(type(service), "_create_document_draft") as create_draft:
            service.process_sync(sync)
        create_draft.assert_not_called()

    def test_existing_xml_invoice_is_linked_without_creating_another_bill(self):
        company = self.env.company
        company.write(
            {
                "l10n_cl_rcv_service_user_id": self.env.user.id,
                "l10n_cl_rcv_automation_enabled": True,
            }
        )
        sync = self._make_sync_with_registro_documents(company)
        partner = self.env["res.partner"].create(
            {"name": "Proveedor XML primero", "vat": "76123456-0"}
        )
        document_type = self.env["l10n_latam.document.type"].search(
            [("code", "=", "33"), ("country_id.code", "=", "CL")], limit=1
        )
        move = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "company_id": company.id,
                "partner_id": partner.id,
                "invoice_date": "2026-09-10",
                "l10n_latam_document_type_id": document_type.id,
                "l10n_latam_document_number": "98002",
            }
        )
        document = self.env["l10n.cl.rcv.document"].create(
            {
                "company_id": self.env.company.id,
                "operation": "purchase",
                "source": "sii",
                "tax_period": "202609",
                "document_type_code": "33",
                "issuer_vat": partner.vat,
                "folio": "98002",
                "sii_state": "REGISTRO",
                "reconciliation_state": "review",
                "last_sync_id": sync.id,
            }
        )

        def matching_bill_count():
            moves = self.env["account.move"].search(
                [
                    ("company_id", "=", company.id),
                    ("move_type", "=", "in_invoice"),
                    ("partner_id", "=", partner.id),
                ]
            )
            return len(
                moves.filtered(
                    lambda candidate: candidate.l10n_latam_document_number == "98002"
                )
            )

        before = matching_bill_count()

        self.env["l10n.cl.rcv.auto.draft"].process_existing_purchases(
            company, [(2026, 9)]
        )

        self.assertEqual(document.move_id, move)
        self.assertEqual(matching_bill_count(), before)

    def test_one_document_error_does_not_stop_following_documents(self):
        period = self.env["l10n.cl.rcv.period"].create(
            {
                "company_id": self.env.company.id,
                "year": 2026,
                "month": 8,
                "operation": "purchase",
            }
        )
        sync = self.env["l10n.cl.rcv.sync"].create(
            {
                "period_id": period.id,
                "company_id": self.env.company.id,
                "operation": "purchase",
                "requested_by": self.env.user.id,
                "source": "sii",
                "state": "success",
            }
        )
        self.env["l10n.cl.rcv.section"].create(
            {"sync_id": sync.id, "sii_state": "Registro", "state": "complete"}
        )
        documents = self.env["l10n.cl.rcv.document"].create(
            [
                {
                    "company_id": self.env.company.id,
                    "operation": "purchase",
                    "source": "sii",
                    "tax_period": "202608",
                    "document_type_code": "33",
                    "issuer_vat": "76123456-0",
                    "folio": folio,
                    "sii_state": "Registro",
                    "last_sync_id": sync.id,
                }
                for folio in ("98003", "98004")
            ]
        )
        service = self.env["l10n.cl.rcv.auto.draft"]
        with patch.object(
            type(service),
            "_create_document_draft",
            side_effect=[RuntimeError("test failure"), None],
        ) as create_draft:
            service.process_sync(sync)

        self.assertEqual(create_draft.call_count, 2)
        self.assertIn("test failure", documents[0].automation_error)
        self.assertFalse(documents[1].automation_error)

    def test_cron_creates_valid_draft_and_records_unresolved_tax_per_document(self):
        company = self.env.company
        company.write(
            {
                "l10n_cl_rcv_service_user_id": self.env.user.id,
                "l10n_cl_rcv_automation_enabled": True,
            }
        )
        expense_account = self.env["account.account"].create(
            {
                "name": "RCV automatic expense",
                "code": "RCVAUTO",
                "account_type": "expense",
                "company_ids": [(6, 0, company.ids)],
            }
        )
        payable_account = self.env["account.account"].create(
            {
                "name": "RCV automatic payable",
                "code": "RCVAPAY",
                "account_type": "liability_payable",
                "reconcile": True,
                "company_ids": [(6, 0, company.ids)],
            }
        )
        journal = self.env["account.journal"].search(
            [("company_id", "=", company.id), ("type", "=", "purchase")],
            limit=1,
        )
        journal.default_account_id = expense_account
        partner = self.env["res.partner"].create(
            {
                "name": "Proveedor compañía automático",
                "vat": "76123456-0",
                "company_type": "company",
            }
        )
        partner.property_account_payable_id = payable_account
        self.env["res.partner"].create(
            {
                "name": "Contacto del proveedor automático",
                "vat": "76123456-0",
                "parent_id": partner.id,
            }
        )

        active_taxes = self.env["account.tax"].search(
            [
                ("company_id", "=", company.id),
                ("type_tax_use", "=", "purchase"),
                ("amount", ">", 0),
                ("active", "=", True),
            ]
        )
        active_taxes.write({"active": False})
        tag_model = self.env["account.account.tag"]
        country = self.env.ref("base.cl")

        def get_tag(name):
            tag = tag_model.search(
                [
                    ("name", "=", name),
                    ("applicability", "=", "taxes"),
                    ("country_id", "=", country.id),
                ],
                limit=1,
            )
            return tag or tag_model.create(
                {
                    "name": name,
                    "applicability": "taxes",
                    "country_id": country.id,
                }
            )

        invoice_tag = get_tag("+IVA Pagado Compras Recuperables")
        refund_tag = get_tag("-IVA Pagado Compras Recuperables")
        tax = self.env["account.tax"].create(
            {
                "name": "RCV automatic recoverable VAT",
                "company_id": company.id,
                "tax_group_id": self.env["account.tax.group"].search([], limit=1).id,
                "l10n_cl_sii_code": 14,
                "type_tax_use": "purchase",
                "amount_type": "percent",
                "amount": 19,
                "invoice_repartition_line_ids": [
                    (0, 0, {"repartition_type": "base"}),
                    (
                        0,
                        0,
                        {
                            "repartition_type": "tax",
                            "tag_ids": [(6, 0, invoice_tag.ids)],
                        },
                    ),
                ],
                "refund_repartition_line_ids": [
                    (0, 0, {"repartition_type": "base"}),
                    (
                        0,
                        0,
                        {
                            "repartition_type": "tax",
                            "tag_ids": [(6, 0, refund_tag.ids)],
                        },
                    ),
                ],
            }
        )

        period = self.env["l10n.cl.rcv.period"].create(
            {
                "company_id": company.id,
                "year": 2026,
                "month": 9,
                "operation": "purchase",
            }
        )
        sync = self.env["l10n.cl.rcv.sync"].create(
            {
                "period_id": period.id,
                "company_id": company.id,
                "operation": "purchase",
                "requested_by": self.env.user.id,
                "source": "sii",
                "state": "success",
            }
        )
        self.env["l10n.cl.rcv.section"].create(
            {"sync_id": sync.id, "sii_state": "Registro", "state": "complete"}
        )
        documents = self.env["l10n.cl.rcv.document"].create(
            [
                {
                    "company_id": company.id,
                    "operation": "purchase",
                    "source": "sii",
                    "tax_period": "202609",
                    "document_type_code": "33",
                    "issuer_vat": partner.vat,
                    "issuer_name": partner.name,
                    "folio": folio,
                    "sii_state": "Registro",
                    "reconciliation_state": (
                        "amount_diff" if folio == "98101" else "missing"
                    ),
                    "last_sync_id": sync.id,
                    "net_amount": 100,
                    "total_amount": 119,
                    **component,
                }
                for folio, component in (
                    ("98101", {"vat_amount": 19, "tax_breakdown_available": True}),
                    ("98102", {"common_vat": 19}),
                )
            ]
        )

        with patch.object(
            type(self.env["account.account"]),
            "_get_most_frequent_account_for_partner",
            return_value=expense_account.id,
        ):
            self.env["l10n.cl.rcv.auto.draft"].process_existing_purchases(
                company, [(2026, 9)]
            )

        valid_document, unresolved_document = documents
        self.assertTrue(valid_document.move_id)
        self.assertEqual(valid_document.move_id.state, "draft")
        self.assertEqual(valid_document.move_id.partner_id, partner)
        self.assertEqual(
            valid_document.move_id.invoice_line_ids.account_id, expense_account
        )
        self.assertEqual(valid_document.move_id.invoice_line_ids.tax_ids, tax)
        self.assertFalse(valid_document.automation_error)
        self.assertFalse(unresolved_document.move_id)
        self.assertIn("impuesto", unresolved_document.automation_error.lower())
        self.assertEqual(unresolved_document.reconciliation_state, "review")

    def test_concurrent_work_for_same_fiscal_identity_is_serialized(self):
        identity = "1|PURCHASE|76123456-0|33|98005"
        first_cursor = self.env.registry.cursor()
        second_acquired = Event()

        first_cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (identity,))

        def acquire_second_lock():
            with self.env.registry.cursor() as second_cursor:
                second_cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))", (identity,)
                )
                second_acquired.set()
                second_cursor.rollback()

        worker = Thread(target=acquire_second_lock)
        worker.start()
        self.assertFalse(second_acquired.wait(0.2))
        first_cursor.rollback()
        self.assertTrue(second_acquired.wait(3))
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        first_cursor.close()
