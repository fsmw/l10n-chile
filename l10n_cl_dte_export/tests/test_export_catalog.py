# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from lxml import etree
from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import Form, tagged
from odoo.tests.common import new_test_user
from odoo.tools import file_path, mute_logger

from odoo.addons.l10n_cl_dte_core.tests.common import DteTestCase


@tagged("post_install", "-at_install")
class TestExportCatalog(DteTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager_only = new_test_user(
            cls.env,
            login="catalog-manager",
            groups="l10n_cl_dte_core.group_dte_manager",
        )
        cls.catalog = cls.env["l10n_cl.dte.export.catalog"].with_user(cls.manager_only)
        cls.accountant = new_test_user(
            cls.env, login="catalog-accountant", groups="account.group_account_invoice"
        )
        cls.internal = new_test_user(
            cls.env, login="catalog-internal", groups="base.group_user"
        )
        cls.values = {
            "kind": "port",
            "code": "9999",
            "name": "Synthetic customs code",
            "source_url": "https://example.invalid/customs",
            "source_date": "2026-01-01",
        }

    def test_xsd_limits_for_every_category(self):
        root = etree.parse(file_path("l10n_cl_dte_xml/static/xsd/DTE_v10.xsd"))
        ns = {"x": "http://www.w3.org/2001/XMLSchema"}
        export = root.xpath('//x:element[@name="Exportaciones"]', namespaces=ns)[0]
        fields = {
            "sale_mode": "CodModVenta",
            "sale_clause": "CodClauVenta",
            "transport": "CodViaTransp",
            "port": "CodPtoEmbarque",
            "package": "CodTpoBultos",
            "unit": "CodUnidPesoBruto",
            "payment": "FmaPagExp",
        }
        for kind, name in fields.items():
            restriction = export.xpath(
                ".//x:element[@name=$name]/x:simpleType/x:restriction",
                name=name,
                namespaces=ns,
            )[0]
            self.assertEqual(restriction.get("base"), "xs:positiveInteger")
            width = int(restriction.find("x:totalDigits", ns).get("value"))
            with self.subTest(kind=kind):
                record = self.catalog.create(
                    {**self.values, "kind": kind, "code": "9" * width}
                )
                self.assertEqual(record._validate_export_code(kind), "9" * width)
                # A narrower code than the field width is valid as well.
                record.code = "98"
                self.assertEqual(record._validate_export_code(kind), "98")
                for code in (
                    "1" + "0" * width,
                    "0",
                    "00",
                    "-1",
                    "1.0",
                    "+1",
                    " 1",
                    "1 ",
                    "١",
                    "１",
                    "²",
                    "A",
                    "1\n",
                ):
                    with (
                        self.subTest(code=repr(code)),
                        self.assertRaises(ValidationError),
                        self.cr.savepoint(),
                    ):
                        self.catalog.create({**self.values, "kind": kind, "code": code})

    def test_category_and_activity_checked_at_use(self):
        record = self.catalog.create(self.values)
        for kind in ("unit", "unknown", False):
            with self.subTest(kind=kind), self.assertRaises(ValidationError):
                record._validate_export_code(kind)
        record.action_archive()
        with self.assertRaises(ValidationError):
            record._validate_export_code("port")
        self.assertFalse(self.catalog.search([("id", "=", record.id)]))
        record.action_unarchive()
        self.assertEqual(record._validate_export_code("port"), "9999")

    def test_invalid_updates_and_batch_create_are_atomic(self):
        record = self.catalog.create(self.values)
        for vals in ({"kind": "unit"}, {"code": "0"}):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                record.write(vals)
            self.assertEqual((record.kind, record.code), ("port", "9999"))
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.catalog.create(
                [{**self.values, "code": "1"}, {**self.values, "code": "-2"}]
            )
        self.assertFalse(
            self.catalog.search([("kind", "=", "port"), ("code", "=", "1")])
        )

    def test_unique_category_code_including_archived(self):
        record = self.catalog.create({**self.values, "code": "99"})
        other = self.catalog.create({**self.values, "kind": "unit", "code": "99"})
        record.action_archive()
        with (
            mute_logger("odoo.sql_db"),
            self.assertRaises(IntegrityError),
            self.cr.savepoint(),
        ):
            self.catalog.create({**self.values, "code": "99"})
        with (
            mute_logger("odoo.sql_db"),
            self.assertRaises(IntegrityError),
            self.cr.savepoint(),
        ):
            other.kind = "port"
        another = self.catalog.create({**self.values, "code": "2"})
        with (
            mute_logger("odoo.sql_db"),
            self.assertRaises(IntegrityError),
            self.cr.savepoint(),
        ):
            another.code = "99"

    def test_required_provenance(self):
        for field in ("name", "code", "kind", "source_url", "source_date"):
            with (
                self.subTest(field=field),
                mute_logger("odoo.sql_db"),
                self.assertRaises(IntegrityError),
                self.cr.savepoint(),
            ):
                self.catalog.create({**self.values, field: False})

    def test_access_matrix_and_shared_catalog(self):
        record = self.catalog.create(self.values)
        record.write({"name": "Updated synthetic code"})
        self.assertNotIn("company_id", self.catalog._fields)
        for user in (self.dte_user, self.accountant, self.manager_only):
            actor_record = record.with_user(user)
            self.assertEqual(actor_record.read(["code"])[0]["code"], "9999")
            with self.subTest(user=user.login), self.assertRaises(AccessError):
                actor_record.unlink()
            if user != self.manager_only:
                with self.assertRaises(AccessError):
                    actor_record.write({"active": False})
                with self.assertRaises(AccessError):
                    self.catalog.with_user(user).create({**self.values, "code": "1"})
        with self.assertRaises(AccessError):
            record.with_user(self.internal).read(["code"])
        with self.assertRaises(AccessError):
            record.with_user(self.internal)._validate_export_code("port")
        company = self.env["res.company"].create(
            {"name": "Synthetic second catalog company"}
        )
        self.manager_only.company_ids |= company
        self.assertEqual(
            self.catalog.with_company(company).search([("id", "=", record.id)]), record
        )

    def test_manager_form_and_catalog_action(self):
        form = Form(self.catalog, view="l10n_cl_dte_export.view_export_catalog_form")
        for field, value in self.values.items():
            setattr(form, field, value)
        record = form.save()
        self.assertEqual(record._validate_export_code("port"), "9999")
        action = self.env.ref("l10n_cl_dte_export.action_export_catalog")
        self.assertEqual(action.res_model, record._name)
        self.assertEqual(action.view_mode, "list,form")
        self.assertEqual(
            self.env.ref("l10n_cl_dte_export.menu_export_catalog").action, action
        )

    def test_official_customs_tables_are_loaded_and_valid(self):
        catalog = self.env["l10n_cl.dte.export.catalog"]
        expected = {"sale_clause": 13, "transport": 9, "unit": 13}
        for kind, count in expected.items():
            records = catalog.search([("kind", "=", kind)]).filtered(
                lambda record: "comext.aduana.cl" in record.source_url
            )
            self.assertEqual(len(records), count, kind)
            for record in records:
                self.assertEqual(record._validate_export_code(kind), record.code)
        fob = self.env.ref("l10n_cl_dte_export.export_catalog_sale_clause_5")
        self.assertEqual((fob.code, fob.name), ("5", "FOB"))
