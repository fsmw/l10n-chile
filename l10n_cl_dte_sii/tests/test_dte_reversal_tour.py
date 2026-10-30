# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingHttpCommon


@tagged("post_install", "-at_install")
class TestDteCreditNoteTour(AccountTestInvoicingHttpCommon):
    @classmethod
    @AccountTestInvoicingHttpCommon.setup_country("cl")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.env.ref("base.user_admin").groups_id |= cls.env.ref(
            "l10n_cl_dte_sii.group_dte_user"
        )
        admin = cls.env.ref("base.user_admin")
        admin.company_ids |= cls.company
        admin.company_id = cls.company
        cls.partner = cls.partner_a
        cls.partner.write(
            {
                "country_id": cls.company.country_id.id,
                "vat": "76543210-3",
                "l10n_cl_sii_taxpayer_type": "1",
            }
        )
        cls.journal = cls.company_data["default_journal_sale"]
        cls.journal.l10n_latam_use_documents = True
        cls.invoice = cls._make_source_invoice()

    @classmethod
    def _make_source_invoice(cls):
        document_type = cls.env["l10n_latam.document.type"].search(
            [("country_id.code", "=", "CL"), ("code", "=", "33")], limit=1
        )
        invoice = cls.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": cls.company.id,
                "journal_id": cls.journal.id,
                "partner_id": cls.partner.id,
                "invoice_date": "2026-10-28",
                "l10n_latam_document_type_id": document_type.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Servicio de origen de nota de crédito",
                            "quantity": 1,
                            "price_unit": 1000,
                            "account_id": cls.company_data[
                                "default_account_revenue"
                            ].id,
                        },
                    )
                ],
            }
        )
        invoice.with_context(l10n_cl_skip_dte_emission=True).action_post()
        return invoice

    def test_accountant_creates_credit_note_from_invoice_form(self):
        before_ids = set(
            self.env["account.move"]
            .sudo()
            .search(
                [
                    ("move_type", "=", "out_refund"),
                    ("reversed_entry_id", "=", self.invoice.id),
                ]
            )
            .ids
        )
        self.start_tour(
            f"/web#id={self.invoice.id}&model=account.move&view_type=form",
            "l10n_cl_dte_credit_note_from_invoice",
            login="admin",
        )
        credit_notes = self.env["account.move"].sudo().search(
            [
                ("move_type", "=", "out_refund"),
                ("reversed_entry_id", "=", self.invoice.id),
            ]
        )
        created = credit_notes.filtered(lambda move: move.id not in before_ids)
        self.assertEqual(len(created), 1)
        credit_note = created.ensure_one()
        self.assertEqual(credit_note.state, "draft")
        self.assertEqual(credit_note.l10n_latam_document_type_id.code, "61")
        self.assertEqual(len(credit_note.l10n_cl_reference_ids), 1)
        reference = credit_note.l10n_cl_reference_ids
        self.assertEqual(
            reference.origin_doc_number, self.invoice.l10n_latam_document_number
        )
        self.assertEqual(reference.reference_doc_code, "3")
