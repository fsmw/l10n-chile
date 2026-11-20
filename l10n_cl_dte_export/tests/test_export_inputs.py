# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import new_test_user

from odoo.addons.l10n_cl_dte_issuance.services.dte_issuance_service import (
    DteIssuanceService,
)
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase


@tagged("post_install", "-at_install")
class TestExportInputs(DteInvoiceTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.document_types |= {
            code: cls.env["l10n_latam.document.type"].search(
                [("country_id", "=", cls.chile.id), ("code", "=", code)], limit=1
            )
            for code in ("110", "111", "112")
        }
        cls.accountant = new_test_user(
            cls.env(su=True),
            login="export-accountant",
            groups="account.group_account_invoice",
            company_id=cls.company.id,
            company_ids=[(6, 0, cls.company.ids)],
        )
        cls.operator = new_test_user(
            cls.env(su=True),
            login="export-operator",
            groups="account.group_account_invoice,l10n_cl_dte_core.group_dte_user",
            company_id=cls.company.id,
            company_ids=[(6, 0, cls.company.ids)],
        )

    def draft(self):
        return self.make_invoice("110", post=False, taxable=False)

    def test_inputs_copy_and_optional_creation(self):
        move = self.draft().with_user(self.operator)
        values = {
            "dte_export_service": "3",
            "dte_export_receiver_id": "FOREIGN-001",
            "dte_export_receiver_extra_id": "EXTRA-001",
            "dte_export_nationality_id": self.env.ref("base.us").id,
            "dte_export_destination_id": self.env.ref("base.us").id,
            "dte_export_rate": 950.1234,
            "dte_export_rate_date": "2026-01-15",
            "dte_export_rate_source": "https://example.invalid/rate",
        }
        move.write(dict(values))
        copied = move.copy()
        for field in values:
            if field.startswith("dte_export_rate"):
                self.assertFalse(copied[field])
            else:
                self.assertEqual(copied[field], move[field], field)
        move.action_post()
        self.assertEqual(move.state, "posted")
        # Export 110 is issuable, but this source lacks the customs data.
        self.assertTrue(move.dte_issuance_available)
        with self.assertRaises(UserError):
            move.issue_dte()
        self.assertFalse(move.dte_document_ids)

    def test_edit_requires_accounting_and_dte_roles(self):
        move = self.draft()
        for user in (self.accountant, self.dte_user):
            with self.subTest(user=user.login), self.assertRaises(AccessError):
                move.with_user(user).write({"dte_export_receiver_id": "DENIED"})
        for code in ("111", "112"):
            move.write(
                {
                    "l10n_latam_document_type_id": self.document_types[code].id,
                    "move_type": "out_refund" if code == "112" else "out_invoice",
                }
            )
            with self.subTest(code=code), self.assertRaises(AccessError):
                move.with_user(self.operator).write({"dte_export_service": "goods"})
            move.write({"dte_export_service": "goods"})

    def test_batch_create_defaults_and_write_rollback(self):
        move = self.draft()
        values = move.copy_data()[0]
        model = self.env["account.move"].with_user(self.accountant)
        before = model.search([])
        with self.assertRaises(AccessError):
            model.create([values, {**values, "dte_export_service": "goods"}])
        self.assertEqual(model.search([]), before)
        with self.assertRaises(AccessError):
            model.with_context(default_dte_export_service="goods").create(
                {
                    key: value
                    for key, value in values.items()
                    if key != "dte_export_service"
                }
            )
        self.assertEqual(model.search([]), before)
        domestic = self.make_invoice(post=False).with_user(self.accountant)
        self.assertTrue(domestic.copy())
        posted = self.make_invoice("110", taxable=False)
        for sources in (move | posted, posted | move):
            with self.assertRaises(UserError):
                sources.write({"dte_export_receiver_id": "DENIED"})
        with self.assertRaises(UserError):
            posted.write({"state": "draft", "dte_export_service": "goods"})
        self.assertFalse(move.dte_export_receiver_id)
        with self.assertRaises(UserError):
            move.write({"dte_export_service": "goods", "state": "posted"})
        self.assertEqual(move.state, "draft")
        self.assertFalse(move.dte_export_service)

    def test_note_type_switch_and_copy_cannot_bypass_manager(self):
        move = self.draft().with_user(self.operator)
        move.dte_export_service = "goods"
        target = {"l10n_latam_document_type_id": self.document_types["111"].id}
        for values in (target, {**target, "dte_export_service": "3"}):
            with self.assertRaises(AccessError):
                move.write(values)
            self.assertEqual(move.dte_document_type_code, "110")
        move.with_user(self.dte_manager).write(target)
        with self.assertRaises(AccessError):
            move.copy()
        with self.assertRaises(AccessError):
            move.write({"l10n_latam_document_type_id": self.document_types["110"].id})
        values = move.copy_data()[0]
        with self.assertRaises(AccessError):
            move.create(values)

    def test_archived_and_hidden_evidence_freezes_inputs(self):
        move = self.draft()
        self.assertFalse(move.dte_document_ids)
        document = self.create_document(
            source_move_id=move.id, document_code="110", active=False
        )
        for target in (False, self.draft().id):
            with self.assertRaises(UserError):
                document.write({"source_move_id": target})
        self.assertEqual(document.source_move_id, move)
        move.invalidate_recordset(["dte_document_ids"])
        self.assertFalse(move.dte_document_ids)
        with self.assertRaises(UserError):
            move.write({"dte_export_rate": 950})
        rule = (
            self.env["ir.rule"]
            .sudo()
            .create(
                {
                    "name": "Synthetic hidden export evidence",
                    "model_id": self.env["ir.model"]._get_id(document._name),
                    "domain_force": repr([("id", "!=", document.id)]),
                }
            )
        )
        with self.assertRaises(AccessError):
            move.with_context(active_test=True).write({"dte_export_service": "goods"})
        service = DteIssuanceService()
        for method in ("prepare", "issue"):
            with patch.object(
                service, "_prepare", side_effect=AssertionError("Elevated work")
            ):
                with self.assertRaises(AccessError):
                    getattr(service, method)(move, None, "110")
        self.assertFalse(move.dte_export_service)
        rule.unlink()

    def test_record_rules_and_company_membership(self):
        move = self.draft().with_user(self.operator)
        for operation in ("read", "write"):
            rule = (
                self.env["ir.rule"]
                .sudo()
                .create(
                    {
                        "name": "Synthetic export source restriction",
                        "model_id": self.env["ir.model"]._get_id("account.move"),
                        "domain_force": repr([("id", "!=", move.id)]),
                        "perm_read": operation == "read",
                        "perm_write": operation == "write",
                        "perm_create": False,
                        "perm_unlink": False,
                    }
                )
            )
            with self.assertRaises(AccessError):
                move.write({"dte_export_receiver_id": "DENIED"})
            rule.unlink()
        self.operator.sudo().company_ids |= self.env.ref("base.main_company")
        with self.assertRaises(AccessError):
            move.with_context(
                allowed_company_ids=self.env.ref("base.main_company").ids
            )._check_export_access("write")
        self.operator.sudo().write(
            {
                "company_id": self.env.ref("base.main_company").id,
                "company_ids": [(6, 0, self.env.ref("base.main_company").ids)],
            }
        )
        with self.assertRaises(AccessError):
            move.with_context(
                allowed_company_ids=self.company.ids
            )._check_export_access("write")
        move.sudo()._check_dte_source_access("110")

    def test_source_hook_precedes_elevation_and_resume(self):
        move = self.draft()
        service = DteIssuanceService()
        self.create_document(source_move_id=move.id, document_code="110")
        for code in ("111", "112"):
            source = self.draft()
            source.write(
                {
                    "l10n_latam_document_type_id": self.document_types[code].id,
                    "move_type": "out_refund" if code == "112" else "out_invoice",
                }
            )
            self.create_document(source_move_id=source.id, document_code=code)
            for method in ("prepare", "issue"):
                with (
                    self.subTest(code=code, method=method),
                    patch.object(
                        service, "_prepare", side_effect=AssertionError("Elevated work")
                    ),
                ):
                    with self.assertRaises(AccessError):
                        getattr(service, method)(
                            source.with_user(self.operator), None, code
                        )
        move._check_dte_source_access("110")
        move.sudo()._check_dte_source_access("110")
        with self.assertRaises(UserError):
            move._check_dte_source_access("111")

    def _catalog(self, kind, code):
        return (
            self.env["l10n_cl.dte.export.catalog"]
            .sudo()
            .create(
                {
                    "kind": kind,
                    "code": code,
                    "name": f"{kind} {code}",
                    "source_url": "https://www.aduana.cl/",
                    "source_date": "2026-01-01",
                }
            )
        )

    def test_customs_inputs_use_their_catalog_and_valid_values(self):
        move = self.draft().with_user(self.operator)
        port = self._catalog("port", "906")
        unit = self._catalog("unit", "06")
        move.write(
            {
                "dte_export_loading_port_id": port.id,
                "dte_export_gross_weight": 1250.5,
                "dte_export_gross_unit_id": unit.id,
                "dte_export_carrier_vat": "76086428-5",
            }
        )
        self.assertEqual(move.dte_export_loading_port_id, port)
        for values in (
            {"dte_export_unloading_port_id": unit.id},
            {"dte_export_net_weight": -1},
            {"dte_export_tare": 10_000_000},
            {"dte_export_carrier_vat": "76086428-0"},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                move.write(values)

    def test_packages_and_supporting_references(self):
        move = self.draft().with_user(self.operator)
        box = self._catalog("package", "13")
        move.write(
            {
                "dte_export_package_ids": [
                    (0, 0, {"type_id": box.id, "quantity": 12, "marks": "ACME"})
                ],
                "dte_export_reference_ids": [
                    (
                        0,
                        0,
                        {
                            "document_type": "807",
                            "folio": "6001234567",
                            "date": "2026-01-10",
                        },
                    )
                ],
            }
        )
        self.assertEqual(move.dte_export_package_ids.quantity, 12)
        reference = {"document_type": "807", "folio": "1", "date": "2026-01-11"}
        for values in (
            {"dte_export_package_ids": [(0, 0, {"type_id": box.id, "quantity": 0})]},
            {
                "dte_export_package_ids": [
                    (0, 0, {"type_id": self._catalog("unit", "01").id, "quantity": 1})
                ]
            },
            {"dte_export_reference_ids": [(0, 0, dict(reference, document_type="33"))]},
            {"dte_export_reference_ids": [(0, 0, reference)]},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                with self.cr.savepoint():
                    move.write(values)
