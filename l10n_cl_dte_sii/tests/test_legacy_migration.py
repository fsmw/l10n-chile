# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hashlib

from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from ..hooks import migrate_legacy_account_moves, post_init_hook


@tagged("post_install", "-at_install")
class TestLegacyDteMigration(AccountTestInvoicingCommon):
    @classmethod
    @AccountTestInvoicingCommon.setup_country("cl")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.partner_a.country_id = cls.company.country_id
        cls.partner_a.vat = "76543210-3"
        cls.partner_a.l10n_cl_sii_taxpayer_type = "1"

    def _ensure_legacy_columns(self):
        legacy_columns = {
            "l10n_cl_sii_send_ident": "text",
            "l10n_cl_dte_partner_status": "varchar",
            "l10n_cl_dte_file": "integer",
            "l10n_cl_sii_send_file": "integer",
        }
        for column, sql_type in legacy_columns.items():
            self.env.cr.execute(
                f'ALTER TABLE account_move ADD COLUMN IF NOT EXISTS "{column}" '
                f"{sql_type}"
            )

    def test_legacy_account_move_evidence_is_copied_idempotently(self):
        self._ensure_legacy_columns()

        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": self.company_data["default_journal_sale"].id,
                "partner_id": self.partner_a.id,
                "invoice_date": "2026-10-28",
            }
        )
        dte = self.env["ir.attachment"].create(
            {
                "name": "legacy-dte.xml",
                "raw": b"<DTE/>",
                "res_model": "account.move",
                "res_id": move.id,
            }
        )
        envelope = self.env["ir.attachment"].create(
            {
                "name": "legacy-envelope.xml",
                "raw": b"<EnvioDTE/>",
                "res_model": "account.move",
                "res_id": move.id,
            }
        )
        original_attachment_evidence = {
            attachment.id: (
                hashlib.sha256(attachment.raw).hexdigest(),
                attachment.store_fname,
            )
            for attachment in (dte | envelope)
        }
        self.env.cr.execute(
            "UPDATE ir_attachment SET company_id = NULL WHERE id IN %s",
            (tuple((dte | envelope).ids),),
        )
        self.env.cr.execute(
            """
            UPDATE account_move
               SET l10n_cl_sii_send_ident = %s,
                   l10n_cl_dte_partner_status = %s,
                   l10n_cl_dte_file = %s,
                   l10n_cl_sii_send_file = %s
             WHERE id = %s
            """,
            ("1234567890", "sent", dte.id, envelope.id, move.id),
        )

        result = post_init_hook(self.env)
        self.env.invalidate_all()

        self.assertEqual(result["dte_attachment"], 1)
        self.assertEqual(result["envelope_attachment"], 1)
        self.assertEqual(result["track_id"], 1)
        self.assertEqual(result["receiver_status"], 1)
        self.assertEqual(result["attachment_company"], 2)
        self.assertEqual(move.l10n_cl_dte_attachment_id, dte)
        self.assertEqual(move.l10n_cl_dte_envelope_attachment_id, envelope)
        self.assertEqual(dte.company_id, self.company)
        self.assertEqual(envelope.company_id, self.company)
        for attachment in dte | envelope:
            self.assertEqual(
                hashlib.sha256(attachment.raw).hexdigest(),
                original_attachment_evidence[attachment.id][0],
            )
            self.assertEqual(
                attachment.store_fname,
                original_attachment_evidence[attachment.id][1],
            )
        self.assertEqual(move.l10n_cl_dte_track_id, "1234567890")
        self.assertEqual(move.l10n_cl_dte_receiver_status, "sent")

        result = migrate_legacy_account_moves(self.env)
        self.assertEqual(sum(result.values()), 0)

    def test_conflicting_legacy_values_are_not_overwritten(self):
        self._ensure_legacy_columns()
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": self.company_data["default_journal_sale"].id,
                "partner_id": self.partner_a.id,
                "invoice_date": "2026-10-28",
            }
        )
        legacy_attachments = self.env["ir.attachment"].create(
            [
                {
                    "name": "legacy-conflict-dte.xml",
                    "raw": b"<DTE>legacy</DTE>",
                    "res_model": "account.move",
                    "res_id": move.id,
                    "company_id": self.company.id,
                },
                {
                    "name": "legacy-conflict-envelope.xml",
                    "raw": b"<EnvioDTE>legacy</EnvioDTE>",
                    "res_model": "account.move",
                    "res_id": move.id,
                    "company_id": self.company.id,
                },
            ]
        )
        canonical_attachments = self.env["ir.attachment"].create(
            [
                {
                    "name": "canonical-dte.xml",
                    "raw": b"<DTE>canonical</DTE>",
                    "res_model": "account.move",
                    "res_id": move.id,
                    "company_id": self.company.id,
                },
                {
                    "name": "canonical-envelope.xml",
                    "raw": b"<EnvioDTE>canonical</EnvioDTE>",
                    "res_model": "account.move",
                    "res_id": move.id,
                    "company_id": self.company.id,
                },
            ]
        )
        move._l10n_cl_write_dte_lifecycle_values(
            {
                "l10n_cl_dte_attachment_id": canonical_attachments[0].id,
                "l10n_cl_dte_envelope_attachment_id": canonical_attachments[1].id,
                "l10n_cl_dte_track_id": "9999999999",
                "l10n_cl_dte_receiver_status": "not_sent",
            }
        )
        self.env.cr.execute(
            """
            UPDATE account_move
               SET l10n_cl_sii_send_ident = %s,
                   l10n_cl_dte_partner_status = %s,
                   l10n_cl_dte_file = %s,
                   l10n_cl_sii_send_file = %s
             WHERE id = %s
            """,
            (
                "1234567890",
                "sent",
                legacy_attachments[0].id,
                legacy_attachments[1].id,
                move.id,
            ),
        )
        move.flush_recordset(
            [
                "l10n_cl_dte_attachment_id",
                "l10n_cl_dte_envelope_attachment_id",
                "l10n_cl_dte_track_id",
                "l10n_cl_dte_receiver_status",
            ]
        )

        result = migrate_legacy_account_moves(self.env)
        self.env.invalidate_all()

        self.assertEqual(sum(result.values()), 0)
        self.assertEqual(move.l10n_cl_dte_attachment_id, canonical_attachments[0])
        self.assertEqual(
            move.l10n_cl_dte_envelope_attachment_id, canonical_attachments[1]
        )
        self.assertEqual(move.l10n_cl_dte_track_id, "9999999999")
        self.assertEqual(move.l10n_cl_dte_receiver_status, "not_sent")
        self.env.cr.execute(
            """
            SELECT l10n_cl_dte_file, l10n_cl_sii_send_file,
                   l10n_cl_sii_send_ident, l10n_cl_dte_partner_status
              FROM account_move
             WHERE id = %s
            """,
            (move.id,),
        )
        self.assertEqual(
            self.env.cr.fetchone(),
            (
                legacy_attachments[0].id,
                legacy_attachments[1].id,
                "1234567890",
                "sent",
            ),
        )

    def test_migration_rejects_dte_attachment_from_another_company(self):
        self._ensure_legacy_columns()
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": self.company_data["default_journal_sale"].id,
                "partner_id": self.partner_a.id,
                "invoice_date": "2026-10-28",
            }
        )
        foreign_company = self.env["res.company"].create(
            {
                "name": "Foreign legacy DTE attachment company",
                "country_id": self.company.country_id.id,
            }
        )
        attachment = self.env["ir.attachment"].create(
            {
                "name": "legacy-foreign-company-dte.xml",
                "raw": b"<DTE/>",
                "res_model": "account.move",
                "res_id": move.id,
                "company_id": foreign_company.id,
            }
        )
        canonical_attachment = self.env["ir.attachment"].create(
            {
                "name": "canonical-company-dte.xml",
                "raw": b"<DTE/>",
                "res_model": "account.move",
                "res_id": move.id,
                "company_id": self.company.id,
            }
        )
        move._l10n_cl_write_dte_lifecycle_values(
            {"l10n_cl_dte_attachment_id": canonical_attachment.id}
        )
        self.env.cr.execute(
            "UPDATE account_move SET l10n_cl_dte_file = %s WHERE id = %s",
            (attachment.id, move.id),
        )

        with self.assertRaisesRegex(RuntimeError, "different company"):
            with self.env.cr.savepoint():
                migrate_legacy_account_moves(self.env)

        self.env.invalidate_all()
        self.assertEqual(move.l10n_cl_dte_attachment_id, canonical_attachment)
        self.assertEqual(attachment.company_id, foreign_company)
        self.env.cr.execute(
            "SELECT l10n_cl_dte_file FROM account_move WHERE id = %s",
            (move.id,),
        )
        self.assertEqual(self.env.cr.fetchone()[0], attachment.id)

    def test_cancelled_legacy_state_remains_a_valid_dte_state(self):
        self._ensure_legacy_columns()
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": self.company_data["default_journal_sale"].id,
                "partner_id": self.partner_a.id,
                "invoice_date": "2026-10-28",
            }
        )
        ted = "<TED><DD><F>123</F><TD>33</TD></DD></TED>"
        move.sudo()._l10n_cl_write_dte_lifecycle_values(
            {
                "l10n_cl_dte_status": "cancelled",
                "l10n_cl_sii_barcode": ted,
            }
        )
        migrate_legacy_account_moves(self.env)
        self.env.invalidate_all()

        self.assertEqual(move.l10n_cl_dte_status, "cancelled")
        self.assertEqual(move.l10n_cl_sii_barcode, ted)
