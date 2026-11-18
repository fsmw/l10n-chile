# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import DteNoteTestCase


@tagged("post_install", "-at_install")
class TestExportWizardDispatch(DteNoteTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.document_types = cls.document_types | {
            code: cls.env["l10n_latam.document.type"].search(
                [("country_id", "=", cls.chile.id), ("code", "=", code)], limit=1
            )
            for code in ("110", "111", "112")
        }

    def wizard(self, moves, kind):
        model = "account.move.reversal" if kind == "credit" else "account.debit.note"
        return self.env[model].create(
            {
                "move_ids": [(6, 0, moves.ids)],
                "date": moves[0].invoice_date,
                "journal_id": moves[0].journal_id.id,
                "reason": "Synthetic adjustment",
            }
        )

    def run_wizard(self, wizard, kind):
        return wizard.reverse_moves() if kind == "credit" else wizard.create_debit()

    def export_document(self, move, code="110", active=True):
        # Routing evidence only; export fiscal acceptance is a separate addon gate.
        return self.env["l10n_cl.electronic.document"].create(
            {
                "name": "Synthetic export routing evidence",
                "company_id": move.company_id.id,
                "sii_environment": "demo",
                "document_code": code,
                "source_key": f"export-wizard-{move.id}-{code}",
                "source_move_id": move.id,
                "active": active,
            }
        )

    def test_export_dispatch_precedes_domestic_targets_without_mutation(self):
        move = self.make_invoice("110", taxable=False)
        document = self.export_document(move)
        action = {
            "type": "ir.actions.act_window",
            "res_model": "l10n_cl.dte.export.adjustment.wizard",
            "view_mode": "form",
            "target": "new",
        }
        observations = []

        def extension(source, kind):
            observations.append((source.ids, source.env.uid, source.env.su, kind))
            return action

        before = self.env["account.move"].search([]).ids
        for kind in ("credit", "debit"):
            wizard = self.wizard(move, kind)
            target_method = f"_dte_{kind}_target"
            with (
                self.subTest(kind=kind),
                patch.object(
                    type(move), "_get_dte_adjustment_action", extension, create=True
                ),
                patch.object(
                    type(wizard),
                    target_method,
                    side_effect=AssertionError("Domestic target must not run"),
                ) as domestic_target,
            ):
                self.assertEqual(self.run_wizard(wizard, kind), action)
                domestic_target.assert_not_called()
            self.assertEqual(self.env["account.move"].search([]).ids, before)
            self.assertEqual(move.state, "posted")
            self.assertEqual(move.dte_document_ids, document)
            self.assertFalse(document.artifact_ids)
            if kind == "credit":
                self.assertFalse(wizard.new_move_ids)
        self.assertEqual(
            observations,
            [(move.ids, self.env.uid, False, kind) for kind in ("credit", "debit")],
        )

    def test_export_without_extension_is_closed_even_without_evidence(self):
        move = self.make_invoice("110", taxable=False)
        before = self.env["account.move"].search([]).ids
        for kind in ("credit", "debit"):
            with (
                self.subTest(kind=kind),
                self.assertRaisesRegex(
                    UserError, "Export adjustments are not available"
                ),
            ):
                self.run_wizard(self.wizard(move, kind), kind)
        self.assertEqual(self.env["account.move"].search([]).ids, before)

    def test_mixed_and_multiple_export_sources_never_reach_extension(self):
        export = self.make_invoice("110", taxable=False)
        other = self.make_invoice("110", taxable=False)
        domestic = self.make_invoice()
        with patch.object(
            type(export), "_get_dte_adjustment_action", create=True
        ) as extension:
            for moves in (export | domestic, domestic | export, export | other):
                for kind in ("credit", "debit"):
                    with self.subTest(moves=moves.ids, kind=kind):
                        wizard = self.wizard(moves, kind)
                        with self.assertRaisesRegex(UserError, "one export invoice"):
                            self.run_wizard(wizard, kind)
            extension.assert_not_called()

    def test_export_notes_cannot_be_adjustment_roots(self):
        for code in ("111", "112"):
            move = self.make_invoice("110", post=False, taxable=False)
            move.write(
                {
                    "move_type": "out_refund" if code == "112" else "out_invoice",
                    "l10n_latam_document_type_id": self.document_types[code].id,
                }
            )
            move.action_post()
            for kind in ("credit", "debit"):
                with (
                    self.subTest(code=code, kind=kind),
                    self.assertRaisesRegex(UserError, "type 110"),
                ):
                    self.run_wizard(self.wizard(move, kind), kind)

    def test_export_replacement_cannot_reach_extension(self):
        move = self.make_invoice("110", taxable=False)
        wizard = self.wizard(move, "credit")
        with patch.object(
            type(move), "_get_dte_adjustment_action", create=True
        ) as extension:
            with self.assertRaisesRegex(UserError, "replacement-invoice"):
                wizard.reverse_moves(is_modify=True)
            extension.assert_not_called()

    def test_export_source_access_checked_before_extension(self):
        move = self.make_invoice("110", taxable=False)
        wizards = {kind: self.wizard(move, kind) for kind in ("credit", "debit")}
        with patch.object(
            type(move), "_get_dte_adjustment_action", create=True
        ) as extension:
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
                for kind, wizard in wizards.items():
                    with self.subTest(operation=operation, kind=kind):
                        with self.assertRaises(AccessError):
                            self.run_wizard(wizard, kind)
                with self.assertRaises(AccessError):
                    move._dispatch_dte_adjustment("credit")
                rule.unlink()
            extension.assert_not_called()

    def test_ambiguous_or_conflicting_evidence_cannot_reach_extension(self):
        move = self.make_invoice("110", taxable=False)
        self.export_document(move)
        self.export_document(move, "111", active=False)
        domestic = self.make_invoice()
        self.export_document(domestic, active=False)
        with patch.object(
            type(move), "_get_dte_adjustment_action", create=True
        ) as extension:
            for source in (move, domestic):
                for kind in ("credit", "debit"):
                    with self.subTest(source=source.id, kind=kind):
                        with self.assertRaises(UserError):
                            self.run_wizard(self.wizard(source, kind), kind)
            extension.assert_not_called()

    def test_domestic_accepted_sources_keep_native_note_execution(self):
        self.company.sudo().account_fiscal_country_id = self.chile
        group = (
            self.env["account.tax.group"]
            .sudo()
            .create({"name": "Synthetic Chile VAT", "country_id": self.chile.id})
        )
        self.tax_19.sudo().write(
            {"country_id": self.chile.id, "tax_group_id": group.id}
        )
        self.company_data["default_journal_sale"].sudo().l10n_latam_use_documents = True
        self.receiver.l10n_cl_sii_taxpayer_type = "1"
        for code in ("33", "34"):
            source, target, _snapshot = self.create_accepted_source(code)
            for kind in ("credit", "debit"):
                with self.subTest(code=code, kind=kind):
                    action = self.run_wizard(self.wizard(source, kind), kind)
                    draft = self.env["account.move"].browse(action["res_id"])
                    self.assertEqual(draft.state, "draft")
                    self.assertEqual(draft.dte_note_target_document_id, target)
                    self.assertEqual(
                        draft.dte_document_type_code, "61" if kind == "credit" else "56"
                    )

    def test_hidden_relations_cannot_change_dispatch_family(self):
        source = self.make_invoice()
        document = self.export_document(source)
        sources = source | self.make_invoice()
        wizards = {kind: self.wizard(sources, kind) for kind in ("credit", "debit")}
        for hidden in (document, source):
            rule = (
                self.env["ir.rule"]
                .sudo()
                .create(
                    {
                        "name": "Synthetic hidden wizard relation",
                        "model_id": self.env["ir.model"]._get_id(hidden._name),
                        "domain_force": repr([("id", "!=", hidden.id)]),
                        "perm_read": True,
                        "perm_write": False,
                        "perm_create": False,
                        "perm_unlink": False,
                    }
                )
            )
            with patch.object(type(source), "_get_dte_adjustment_action") as extension:
                for kind, wizard in wizards.items():
                    with self.subTest(hidden=hidden._name, kind=kind):
                        wizard.invalidate_recordset(["move_ids"])
                        sources.invalidate_recordset(["dte_document_ids"])
                        visible = (
                            wizard.move_ids
                            if hidden._name == "account.move"
                            else sources.dte_document_ids
                        )
                        self.assertNotIn(hidden, visible)
                        with self.assertRaises(UserError):
                            self.run_wizard(wizard, kind)
                extension.assert_not_called()
            rule.unlink()
