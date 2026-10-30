# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestDteCaf(AccountTestInvoicingCommon):
    @classmethod
    @AccountTestInvoicingCommon.setup_country("cl")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.env.user.write(
            {"groups_id": [(4, cls.env.ref("l10n_cl_dte_sii.group_dte_manager").id)]}
        )
        cls.company.partner_id.vat = "76543210-3"
        cls.document_type = cls.env["l10n_latam.document.type"].search(
            [("country_id.code", "=", "CL"), ("code", "=", "33")], limit=1
        )

    def _caf_xml(self, start=100, end=110):
        return (
            "<AUTORIZACION><CAF><DA><RE>76543210-3</RE><TD>33</TD><RNG>"
            f"<D>{start}</D><H>{end}</H></RNG></DA></CAF></AUTORIZACION>"
        ).encode()

    def test_caf_import_reads_range_and_serializes_folio_reservation(self):
        caf = self.env["l10n_cl.dte.caf"].create_from_xml(
            "caf.xml", self._caf_xml(), self.company
        )
        self.assertEqual(caf.document_type_id, self.document_type)
        self.assertEqual((caf.start_nb, caf.final_nb, caf.next_folio), (100, 110, 100))
        caf.action_enable()

        self.assertEqual(caf._reserve_next_folio(), 100)
        self.assertEqual(caf.next_folio, 101)

    def test_caf_reservation_skips_folio_already_used_by_issued_invoice(self):
        self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.partner_a.id,
                "company_id": self.company.id,
                "journal_id": self.company_data["default_journal_sale"].id,
                "invoice_date": "2026-10-29",
                "l10n_latam_document_type_id": self.document_type.id,
                "name": "33 100",
            }
        )
        caf = self.env["l10n_cl.dte.caf"].create_from_xml(
            "caf.xml", self._caf_xml(start=100, end=101), self.company
        )
        caf.action_enable()

        self.assertEqual(caf._reserve_next_folio(), 101)
        self.assertEqual(caf.next_folio, 102)

    def test_caf_import_rejects_foreign_company_rut(self):
        with self.assertRaises(ValidationError):
            self.env["l10n_cl.dte.caf"].create_from_xml(
                "caf.xml",
                self._caf_xml().replace(b"76543210-3", b"76086428-5"),
                self.company,
            )

    def test_two_active_caf_ranges_cannot_overlap(self):
        model = self.env["l10n_cl.dte.caf"]
        first = model.create_from_xml("first.xml", self._caf_xml(), self.company)
        first.action_enable()
        second = model.create_from_xml(
            "second.xml", self._caf_xml(start=105, end=120), self.company
        )
        with self.assertRaises(ValidationError):
            second.action_enable()
