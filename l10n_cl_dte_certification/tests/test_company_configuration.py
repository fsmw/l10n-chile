# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
from datetime import date

from lxml import etree

from odoo.exceptions import UserError

from ..services.access import check_certification_access
from .common import DteCertificationTestCase


class TestCertificationCompanyConfiguration(DteCertificationTestCase):
    def test_certification_environment_prepares_idempotent_clp_sales_journal(self):
        company = self.company
        journal = company.dte_certification_journal_id

        self.assertTrue(journal)
        self.assertEqual(journal.type, "sale")
        self.assertEqual(journal.company_id, company)
        self.assertEqual(journal.currency_id.name, "CLP")
        self.assertTrue(journal.l10n_latam_use_documents)

        company._ensure_certification_accounting_defaults()
        self.assertEqual(company.dte_certification_journal_id, journal)
        self.assertEqual(
            self.env["account.journal"].search_count(
                [
                    ("company_id", "=", company.id),
                    ("type", "=", "sale"),
                    ("name", "=", "DTE Certification"),
                ]
            ),
            1,
        )

    def test_availability_follows_environment_without_an_opt_in(self):
        company = self.company.with_user(self.certification_manager)
        journal = company.dte_certification_journal_id
        move_count = self.env["account.move"].search_count(
            [("company_id", "=", company.id)]
        )
        for environment in ("demo", "certification", "production", "certification"):
            with self.subTest(environment=environment):
                company.write({"dte_sii_environment": environment})
                self.assertEqual(
                    company.dte_certification_enabled, environment == "certification"
                )
                if environment == "certification":
                    # A DTE manager needs no separate administrator enablement.
                    manager_company = company.with_user(self.dte_manager)
                    check_certification_access(manager_company.env, manager_company)
                else:
                    with self.assertRaises(UserError):
                        check_certification_access(company.env, company)
                self.assertEqual(company.dte_certification_journal_id, journal)
        self.assertEqual(
            self.env["account.move"].search_count([("company_id", "=", company.id)]),
            move_count,
        )

    def test_create_in_certification_prepares_defaults_automatically(self):
        company = self.env["res.company"].create(
            {
                "name": "Automatic certification configuration",
                "country_id": self.env.ref("base.cl").id,
                "currency_id": self.env.ref("base.CLP").id,
                "vat": "12345678-5",
                "dte_sii_environment": "certification",
            }
        )
        self.assertTrue(company.dte_certification_enabled)
        self.assertEqual(company.dte_certification_journal_id.company_id, company)
        self.assertEqual(company.dte_certification_journal_id.currency_id.name, "CLP")
        self.assertFalse(company.dte_sii_sender_vat)

    def test_legacy_flag_cannot_override_environment(self):
        company = self.company.with_user(self.certification_manager)
        for environment, value in (("certification", False), ("demo", True)):
            with self.subTest(environment=environment):
                company.write({"dte_sii_environment": environment})
                with self.assertRaisesRegex(
                    UserError, "determined by the SII environment"
                ):
                    company.write({"dte_certification_enabled": value})
                self.assertEqual(
                    company.dte_certification_enabled, environment == "certification"
                )
        with self.assertRaisesRegex(UserError, "determined by the SII environment"):
            self.env["res.company"].create(
                {
                    "name": "Conflicting legacy enablement",
                    "dte_sii_environment": "demo",
                    "dte_certification_enabled": True,
                }
            )

    def test_company_form_has_no_separate_enablement_switch(self):
        view = (
            self.env["res.company"]
            .with_user(self.certification_manager)
            .get_view(self.env.ref("base.view_company_form").id, "form")
        )
        arch = etree.fromstring(view["arch"])
        self.assertFalse(arch.xpath("//field[@name='dte_certification_enabled']"))
        groups = arch.xpath("//group[@name='dte_certification_configuration']")
        self.assertEqual(len(groups), 1)
        self.assertEqual(
            groups[0].get("invisible"), "dte_sii_environment != 'certification'"
        )

    def test_set_configuration_is_derived_from_company(self):
        company = self.company
        company.write(
            {
                "dte_sii_resolution_number": "TEST-RESOLUTION",
                "dte_sii_resolution_date": "2026-01-01",
                "dte_certification_resolution_date": "2026-02-03",
                "dte_sii_sender_vat": "12345678-5",
            }
        )
        certification_set = self.env["l10n_cl.dte.certification.set"].new(
            {"company_id": company.id}
        )

        self.assertEqual(
            certification_set.journal_id, company.dte_certification_journal_id
        )
        self.assertEqual(
            certification_set.vat_tax_id, company.dte_certification_vat_tax_id
        )
        self.assertEqual(
            certification_set.exempt_tax_id,
            company.dte_certification_exempt_tax_id,
        )
        self.assertEqual(certification_set.resolution_number, "0")
        self.assertEqual(company.dte_sii_resolution_number, "TEST-RESOLUTION")
        self.assertEqual(certification_set.resolution_date, date(2026, 2, 3))
        self.assertEqual(certification_set.sender_vat, "12345678-5")

    def test_sender_is_not_guessed_from_company_rut(self):
        company = self.company
        company.write(
            {
                "dte_sii_sender_vat": False,
            }
        )

        company._ensure_certification_accounting_defaults()

        self.assertFalse(company.dte_sii_sender_vat)

    def test_saved_set_refreshes_related_envelope_values(self):
        source = b"Certification configuration relation test"
        source_digest = hashlib.sha256(source).hexdigest()
        certification_set = (
            self.env["l10n_cl.dte.certification.set"]
            .with_user(self.certification_manager)
            ._create_imported(
                {
                    "name": "Configuration relation test",
                    "company_id": self.company.id,
                    "sii_environment": "certification",
                    "source_file": base64.b64encode(source),
                    "source_filename": "configuration.txt",
                    "source_digest": source_digest,
                    "source_byte_count": len(source),
                    "source_encoding": "utf-8",
                    "parser_profile": "sii_txt_v1",
                    "parser_version": "test",
                    "attention_number": "configuration-test",
                }
            )
        )
        self.company.write(
            {
                "dte_sii_resolution_number": "80",
                "dte_sii_resolution_date": "2014-08-22",
                "dte_certification_resolution_date": "2026-08-14",
            }
        )
        certification_set.invalidate_recordset(["resolution_number", "resolution_date"])

        self.assertEqual(certification_set.resolution_number, "0")
        self.assertEqual(certification_set.resolution_date, date(2026, 8, 14))
        certification_set.write({"resolution_date": "2026-08-15"})
        self.assertEqual(
            self.company.dte_certification_resolution_date, date(2026, 8, 15)
        )
        self.assertEqual(self.company.dte_sii_resolution_date, date(2014, 8, 22))

    def test_production_resolution_does_not_fill_certification_date(self):
        self.company.write(
            {
                "dte_sii_resolution_date": "2014-08-22",
                "dte_certification_resolution_date": False,
            }
        )
        self.company._ensure_certification_accounting_defaults()
        certification_set = self.env["l10n_cl.dte.certification.set"].new(
            {"company_id": self.company.id}
        )
        self.assertFalse(certification_set.resolution_date)
        self.assertEqual(self.company.dte_sii_resolution_date, date(2014, 8, 22))

    def test_set_view_exposes_certification_date_with_configuration_guard(self):
        view = self.env["l10n_cl.dte.certification.set"].get_view(
            self.env.ref("l10n_cl_dte_certification.certification_set_view_form").id,
            "form",
        )
        arch = etree.fromstring(view["arch"])
        date_field = arch.xpath("//field[@name='resolution_date']")
        self.assertEqual(len(date_field), 1)
        self.assertEqual(
            date_field[0].get("readonly"),
            "not can_operate or not can_edit_envelope",
        )
