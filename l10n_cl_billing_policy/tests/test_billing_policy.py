# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestBillingPolicy(TransactionCase):
    def test_default_policy_is_invoice(self):
        partner = self.env["res.partner"].create(
            {"name": "Company", "is_company": True}
        )
        self.assertEqual(partner.invoicing_policy, "invoice")

    def test_company_cannot_use_ticket(self):
        with self.assertRaises(ValidationError):
            self.env["res.partner"].create(
                {"name": "Company", "is_company": True, "invoicing_policy": "ticket"}
            )

    def test_individual_cannot_use_invoice(self):
        with self.assertRaises(ValidationError):
            self.env["res.partner"].create(
                {
                    "name": "Person",
                    "is_company": False,
                    "invoicing_policy": "invoice",
                }
            )

    def test_individual_can_use_ticket(self):
        partner = self.env["res.partner"].create(
            {"name": "Person", "is_company": False, "invoicing_policy": "ticket"}
        )
        self.assertEqual(partner.invoicing_policy, "ticket")

    def test_contact_follows_root_company(self):
        company = self.env["res.partner"].create(
            {"name": "Company", "is_company": True}
        )
        contact = self.env["res.partner"].create(
            {
                "name": "Contact",
                "parent_id": company.id,
                "invoicing_policy": "eguide",
            }
        )
        self.assertEqual(contact.invoicing_policy, "eguide")
        with self.assertRaises(ValidationError):
            contact.invoicing_policy = "ticket"

    def test_onchange_switches_by_company_type(self):
        person = self.env["res.partner"].new({"name": "Person", "is_company": False})
        person._onchange_invoicing_policy()
        self.assertEqual(person.invoicing_policy, "ticket")
        company = self.env["res.partner"].new({"name": "Company", "is_company": True})
        company._onchange_invoicing_policy()
        self.assertEqual(company.invoicing_policy, "invoice")
