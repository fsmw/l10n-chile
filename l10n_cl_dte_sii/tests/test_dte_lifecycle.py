# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import time
from unittest.mock import patch

from odoo import api
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.account.tests.common import (
    AccountTestInvoicingCommon,
    AccountTestInvoicingHttpCommon,
)

from ..services.sii_client import SiiTransportError


@tagged("post_install", "-at_install")
class TestDteDemoLifecycle(AccountTestInvoicingCommon):
    @classmethod
    @AccountTestInvoicingCommon.setup_country("cl")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.env.user.write(
            {"groups_id": [(4, cls.env.ref("l10n_cl_dte_sii.group_dte_manager").id)]}
        )
        cls.company.l10n_cl_dte_environment = "SIIDEMO"
        cls.partner_a.country_id = cls.company.country_id
        cls.partner_a.vat = "76543210-3"
        cls.partner_a.l10n_cl_sii_taxpayer_type = "1"

    def test_dte_status_and_track_id_cannot_be_forged_through_orm(self):
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": self.company_data["default_journal_sale"].id,
                "partner_id": self.partner_a.id,
                "invoice_date": "2026-10-28",
            }
        )
        with self.assertRaisesRegex(UserError, "sólo se actualizan"):
            move.with_context(_l10n_cl_dte_internal=True).write(
                {"l10n_cl_dte_status": "accepted"}
            )
        with self.assertRaisesRegex(UserError, "sólo se actualizan"):
            move.write({"l10n_cl_dte_track_id": "1234567890"})
        with self.assertRaisesRegex(UserError, "flujos autorizados"):
            move.write({"l10n_cl_dte_folio": 42})
        with self.assertRaisesRegex(UserError, "flujos autorizados"):
            move.write({"l10n_cl_dte_envelope_attachment_id": False})
        with self.assertRaisesRegex(UserError, "no pueden establecerse"):
            self.env["account.move"].create(
                {
                    "move_type": "out_invoice",
                    "l10n_cl_dte_status": "accepted",
                }
            )

    def test_si_demo_send_and_status_are_single_use_and_never_use_network(self):
        journal = self.company_data["default_journal_sale"]
        document_type = self.env["l10n_latam.document.type"].search(
            [("country_id.code", "=", "CL"), ("code", "=", "33")], limit=1
        )
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": journal.id,
                "partner_id": self.partner_a.id,
                "invoice_date": "2026-10-28",
                "l10n_latam_document_type_id": document_type.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Servicio de prueba",
                            "quantity": 1,
                            "price_unit": 1000,
                            "account_id": self.company_data[
                                "default_account_revenue"
                            ].id,
                        },
                    )
                ],
            }
        )
        move.with_context(l10n_cl_skip_dte_emission=True).action_post()
        envelope = self.env["ir.attachment"].create(
            {
                "name": "sobre-demo.xml",
                "raw": b"<EnvioDTE/>",
                "mimetype": "application/xml",
                "res_model": "account.move",
                "res_id": move.id,
                "company_id": self.company.id,
            }
        )
        move._l10n_cl_write_dte_lifecycle_values(
            {
                "l10n_cl_dte_folio": 100,
                "l10n_cl_dte_envelope_attachment_id": envelope.id,
                "l10n_cl_dte_status": "not_sent",
            }
        )

        move.action_l10n_cl_dte_send()
        self.assertEqual(move.l10n_cl_dte_status, "ask_for_status")
        self.assertEqual(move.l10n_cl_dte_track_id, f"DEMO-{move.id}-100")
        with self.assertRaises(UserError):
            move.action_l10n_cl_dte_send()

        move.action_l10n_cl_dte_check_status()
        self.assertEqual(move.l10n_cl_dte_status, "accepted")
        self.assertTrue(move.l10n_cl_dte_last_status_check)

    def test_uncertain_boleta_track_id_is_saved_and_can_be_reconciled(self):
        self.company.l10n_cl_dte_environment = "SIITEST"
        journal = self.company_data["default_journal_sale"]
        journal.write(
            {"l10n_latam_use_documents": True, "l10n_cl_dte_point_of_sale": "online"}
        )
        document_type = self.env["l10n_latam.document.type"].search(
            [("country_id.code", "=", "CL"), ("code", "=", "39")], limit=1
        )
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": journal.id,
                "partner_id": self.partner_a.id,
                "invoice_date": "2026-10-28",
                "l10n_latam_document_type_id": document_type.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Boleta de prueba",
                            "quantity": 1,
                            "price_unit": 1000,
                            "account_id": self.company_data[
                                "default_account_revenue"
                            ].id,
                        },
                    )
                ],
            }
        )
        move.with_context(l10n_cl_skip_dte_emission=True).action_post()
        envelope = self.env["ir.attachment"].create(
            {
                "name": "boleta-uncertain.xml",
                "raw": b"<EnvioBOLETA/>",
                "mimetype": "application/xml",
                "res_model": "account.move",
                "res_id": move.id,
                "company_id": self.company.id,
            }
        )
        move._l10n_cl_write_dte_lifecycle_values(
            {
                "l10n_cl_dte_folio": 123,
                "l10n_cl_dte_envelope_attachment_id": envelope.id,
                "l10n_cl_dte_status": "not_sent",
            }
        )

        with (
            patch.object(
                type(self.company), "_get_digital_signature", return_value=object()
            ),
            patch(
                "odoo.addons.l10n_cl_dte_sii.models.account_move.SiiDteClient"
            ) as client_class,
        ):
            client_class.return_value.send.side_effect = SiiTransportError(
                "REST receipt is ambiguous", outcome_unknown=True, track_id="678"
            )
            move.action_l10n_cl_dte_send()

            self.assertEqual(move.l10n_cl_dte_status, "error")
            self.assertEqual(move.l10n_cl_dte_track_id, "678")
            with self.assertRaises(UserError):
                move.action_l10n_cl_dte_send()
            self.assertEqual(client_class.return_value.send.call_count, 1)

            client_class.return_value.check_status.return_value = {
                "status": "accepted",
                "diagnostic": "",
            }
            move.action_l10n_cl_dte_check_status()

        self.assertEqual(move.l10n_cl_dte_status, "accepted")

    def test_status_query_persists_sii_response_code_attention_and_counts(self):
        self.company.l10n_cl_dte_environment = "SIITEST"
        move = self._uncertain_invoice()
        move._l10n_cl_write_dte_lifecycle_values(
            {
                "l10n_cl_dte_status": "ask_for_status",
                "l10n_cl_dte_track_id": "1234567890",
            }
        )
        response = {
            "status": "objected",
            "diagnostic": "Documento recibido con reparos.",
            "sii_status_code": "EPR",
            "sii_attention_number": "AT-42",
            "sii_document_type": "33",
            "sii_informed_count": 1,
            "sii_accepted_count": 0,
            "sii_rejected_count": 0,
            "sii_objection_count": 1,
        }
        unknown_response = {
            "status": "ask_for_status",
            "diagnostic": "The SII returned an unrecognized status code.",
            "sii_status_code": "NEW-CODE",
            "sii_attention_number": "AT-43",
        }
        with (
            patch.object(
                type(self.company), "_get_digital_signature", return_value=object()
            ),
            patch(
                "odoo.addons.l10n_cl_dte_sii.models.account_move.SiiDteClient"
            ) as client_class,
        ):
            client_class.return_value.check_status.return_value = response

            move.action_l10n_cl_dte_check_status()

        self.assertEqual(move.l10n_cl_dte_status, "objected")
        self.assertEqual(move.l10n_cl_dte_sii_status_code, "EPR")
        self.assertEqual(move.l10n_cl_dte_sii_attention_number, "AT-42")
        self.assertEqual(move.l10n_cl_dte_sii_document_type, "33")
        self.assertTrue(move.l10n_cl_dte_sii_has_summary)
        self.assertEqual(move.l10n_cl_dte_sii_informed_count, 1)
        self.assertEqual(move.l10n_cl_dte_sii_accepted_count, 0)
        self.assertEqual(move.l10n_cl_dte_sii_rejected_count, 0)
        self.assertEqual(move.l10n_cl_dte_sii_objection_count, 1)
        self.assertEqual(move.l10n_cl_dte_diagnostic, "Documento recibido con reparos.")

        move._l10n_cl_write_dte_lifecycle_values(
            {"l10n_cl_dte_status": "ask_for_status"}
        )
        with (
            patch.object(
                type(self.company), "_get_digital_signature", return_value=object()
            ),
            patch(
                "odoo.addons.l10n_cl_dte_sii.models.account_move.SiiDteClient"
            ) as client_class,
        ):
            client_class.return_value.check_status.return_value = unknown_response
            move.action_l10n_cl_dte_check_status()

        self.assertEqual(move.l10n_cl_dte_status, "ask_for_status")
        self.assertEqual(move.l10n_cl_dte_sii_status_code, "NEW-CODE")
        self.assertEqual(move.l10n_cl_dte_sii_attention_number, "AT-43")
        self.assertFalse(move.l10n_cl_dte_sii_has_summary)

        move._l10n_cl_write_dte_lifecycle_values(
            {"l10n_cl_dte_status": "ask_for_status"}
        )
        error_response = {
            "status": "ask_for_status",
            "diagnostic": "Error de consulta",
            "sii_status_code": "-11",
            "sii_server_code": "0",
            "sii_sql_code": "0",
            "sii_error_code": "1",
            "sii_attention_number": "AT-ERR",
        }
        with (
            patch.object(
                type(self.company), "_get_digital_signature", return_value=object()
            ),
            patch(
                "odoo.addons.l10n_cl_dte_sii.models.account_move.SiiDteClient"
            ) as client_class,
        ):
            client_class.return_value.check_status.return_value = error_response
            move.action_l10n_cl_dte_check_status()

        self.assertEqual(move.l10n_cl_dte_status, "ask_for_status")
        self.assertEqual(move.l10n_cl_dte_sii_status_code, "-11")
        self.assertEqual(move.l10n_cl_dte_sii_server_code, "0")
        self.assertEqual(move.l10n_cl_dte_sii_sql_code, "0")
        self.assertEqual(move.l10n_cl_dte_sii_error_code, "1")
        self.assertFalse(move.l10n_cl_dte_sii_has_summary)

    def test_status_query_transport_failure_is_persisted_without_changing_state(self):
        self.company.l10n_cl_dte_environment = "SIITEST"
        move = self._uncertain_invoice()
        move._l10n_cl_write_dte_lifecycle_values(
            {
                "l10n_cl_dte_status": "ask_for_status",
                "l10n_cl_dte_track_id": "1234567890",
            }
        )
        with (
            patch.object(
                type(self.company), "_get_digital_signature", return_value=object()
            ),
            patch(
                "odoo.addons.l10n_cl_dte_sii.models.account_move.SiiDteClient"
            ) as client_class,
        ):
            client_class.return_value.check_status.side_effect = SiiTransportError(
                "The SII status query timed out."
            )
            action = move.action_l10n_cl_dte_check_status()

        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["message"], "The SII status query timed out.")
        self.assertEqual(action["params"]["type"], "warning")
        self.assertEqual(move.l10n_cl_dte_status, "ask_for_status")
        self.assertEqual(
            move.l10n_cl_dte_last_status_error, "The SII status query timed out."
        )
        self.assertTrue(move.l10n_cl_dte_last_status_check)

    def test_status_query_token_response_code_is_persisted(self):
        self.company.l10n_cl_dte_environment = "SIITEST"
        move = self._uncertain_invoice()
        move._l10n_cl_write_dte_lifecycle_values(
            {
                "l10n_cl_dte_status": "ask_for_status",
                "l10n_cl_dte_track_id": "1234567890",
            }
        )
        with (
            patch.object(
                type(self.company), "_get_digital_signature", return_value=object()
            ),
            patch(
                "odoo.addons.l10n_cl_dte_sii.models.account_move.SiiDteClient"
            ) as client_class,
        ):
            client_class.return_value.check_status.side_effect = SiiTransportError(
                "SII authentication was rejected during status query (TOKEN=002).",
                response_metadata={
                    "sii_status_code": "002",
                    "sii_token_code": "002",
                },
            )
            action = move.action_l10n_cl_dte_check_status()

        self.assertEqual(action["tag"], "display_notification")
        self.assertIn("TOKEN=002", action["params"]["message"])
        self.assertEqual(move.l10n_cl_dte_status, "ask_for_status")
        self.assertEqual(move.l10n_cl_dte_sii_status_code, "002")
        self.assertEqual(move.l10n_cl_dte_sii_token_code, "002")
        self.assertFalse(move.l10n_cl_dte_sii_has_summary)

    def _uncertain_invoice(self):
        document_type = self.env["l10n_latam.document.type"].search(
            [("country_id.code", "=", "CL"), ("code", "=", "33")], limit=1
        )
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": self.company_data["default_journal_sale"].id,
                "partner_id": self.partner_a.id,
                "invoice_date": "2026-10-28",
                "l10n_latam_document_type_id": document_type.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Servicio con envío incierto",
                            "quantity": 1,
                            "price_unit": 1000,
                            "account_id": self.company_data[
                                "default_account_revenue"
                            ].id,
                        },
                    )
                ],
            }
        )
        move.with_context(l10n_cl_skip_dte_emission=True).action_post()
        move._l10n_cl_write_dte_lifecycle_values(
            {
                "l10n_cl_dte_folio": 200,
                "l10n_cl_dte_status": "error",
                "l10n_cl_dte_diagnostic": "Timeout luego del despacho; sin TrackID.",
            }
        )
        return move

    def test_uncertain_invoice_reconcile_received(self):
        move = self._uncertain_invoice()
        action = move.action_l10n_cl_dte_reconcile_uncertain()
        self.assertEqual(action["target"], "new")
        self.assertEqual(action["context"]["default_move_id"], move.id)
        wizard = self.env[action["res_model"]].create(
            {
                "move_id": move.id,
                "resolution": "received",
                "track_id": "1234567890",
                "evidence": (
                    "Consulta manual en el portal SII; recepción y TrackID visibles."
                ),
            }
        )

        wizard.action_confirm()

        self.assertEqual(move.l10n_cl_dte_status, "ask_for_status")
        self.assertEqual(move.l10n_cl_dte_track_id, "1234567890")
        reconciliation = move.l10n_cl_dte_reconciliation_ids
        self.assertEqual(len(reconciliation), 1)
        self.assertEqual(reconciliation.resolution, "received")
        self.assertEqual(
            reconciliation.previous_diagnostic,
            "Timeout luego del despacho; sin TrackID.",
        )
        self.assertIn("portal SII", reconciliation.evidence)
        with self.assertRaises(UserError):
            move.action_l10n_cl_dte_send()

    def test_malformed_track_id_can_be_reconciled_as_received(self):
        move = self._uncertain_invoice()
        move._l10n_cl_write_dte_lifecycle_values({"l10n_cl_dte_track_id": "12345"})
        wizard = self.env["l10n_cl.dte.reconciliation.wizard"].create(
            {
                "move_id": move.id,
                "resolution": "received",
                "track_id": "1234567890",
                "evidence": (
                    "El portal SII confirma recepción con el TrackID válido "
                    "1234567890; la respuesta inicial traía 12345."
                ),
            }
        )

        wizard.action_confirm()

        self.assertEqual(move.l10n_cl_dte_status, "ask_for_status")
        self.assertEqual(move.l10n_cl_dte_track_id, "1234567890")
        reconciliation = move.l10n_cl_dte_reconciliation_ids
        self.assertEqual(reconciliation.previous_track_id, "12345")
        self.assertEqual(reconciliation.track_id, "1234567890")

    def test_malformed_track_id_cannot_authorize_not_received_resend(self):
        move = self._uncertain_invoice()
        move._l10n_cl_write_dte_lifecycle_values({"l10n_cl_dte_track_id": "12345"})
        wizard = self.env["l10n_cl.dte.reconciliation.wizard"].create(
            {
                "move_id": move.id,
                "resolution": "not_received",
                "evidence": "El operador no encontró una consulta concluyente.",
            }
        )

        with self.assertRaises(UserError):
            wizard.action_confirm()

        self.assertEqual(move.l10n_cl_dte_status, "error")
        self.assertEqual(move.l10n_cl_dte_track_id, "12345")
        self.assertFalse(move.l10n_cl_dte_reconciliation_ids)

    def test_uncertain_invoice_reconcile_not_received(self):
        move = self._uncertain_invoice()
        wizard = self.env["l10n_cl.dte.reconciliation.wizard"].create(
            {
                "move_id": move.id,
                "resolution": "not_received",
                "evidence": (
                    "Consulta por RUT, tipo y folio en SII; no existe recepción."
                ),
            }
        )

        wizard.action_confirm()

        self.assertEqual(move.l10n_cl_dte_status, "not_sent")
        self.assertFalse(move.l10n_cl_dte_track_id)
        self.assertEqual(move.l10n_cl_dte_reconciliation_ids.resolution, "not_received")
        self.assertIn("reenvío autorizado", move.l10n_cl_dte_diagnostic)

    def test_second_stale_reconciliation_wizard_cannot_resolve_twice(self):
        move = self._uncertain_invoice()
        wizard_model = self.env["l10n_cl.dte.reconciliation.wizard"]
        values = {
            "move_id": move.id,
            "resolution": "not_received",
            "evidence": "Consulta manual previa al primer registro de conciliación.",
        }
        first_wizard = wizard_model.create(values)
        stale_wizard = wizard_model.create(values)

        first_wizard.action_confirm()

        with self.assertRaises(UserError):
            stale_wizard.action_confirm()
        self.assertEqual(move.l10n_cl_dte_status, "not_sent")
        self.assertEqual(len(move.l10n_cl_dte_reconciliation_ids), 1)

    def test_concurrent_transaction_cannot_reconcile_while_lifecycle_is_locked(self):
        move = self._uncertain_invoice()
        wizard = self.env["l10n_cl.dte.reconciliation.wizard"].create(
            {
                "move_id": move.id,
                "resolution": "not_received",
                "evidence": "Consulta manual SII; recepción ausente para el folio.",
            }
        )
        first_cursor = self.env.registry.cursor()
        first_move = api.Environment(first_cursor, self.env.uid, {})[
            "account.move"
        ].browse(move.id)
        try:
            first_move._lock_dte_lifecycle()
            with self.assertRaisesRegex(UserError, "Otro usuario está enviando"):
                wizard.action_confirm()
            self.assertFalse(move.l10n_cl_dte_reconciliation_ids)
            first_cursor.rollback()

            wizard.action_confirm()

            self.assertEqual(move.l10n_cl_dte_status, "not_sent")
            self.assertEqual(len(move.l10n_cl_dte_reconciliation_ids), 1)
        finally:
            first_cursor.close()
    def test_concurrent_transaction_cannot_send_while_lifecycle_is_locked(self):
        move = self._uncertain_invoice()
        envelope = self.env["ir.attachment"].create(
            {
                "name": "sobre-concurrente.xml",
                "raw": b"<EnvioDTE/>",
                "mimetype": "application/xml",
                "res_model": "account.move",
                "res_id": move.id,
                "company_id": self.company.id,
            }
        )
        move._l10n_cl_write_dte_lifecycle_values(
            {
                "l10n_cl_dte_status": "not_sent",
                "l10n_cl_dte_envelope_attachment_id": envelope.id,
            }
        )
        first_cursor = self.env.registry.cursor()
        first_move = api.Environment(first_cursor, self.env.uid, {})[
            "account.move"
        ].browse(move.id)
        try:
            first_move._lock_dte_lifecycle()
            with (
                patch(
                    "odoo.addons.l10n_cl_dte_sii.models.account_move.SiiDteClient"
                ) as client_class,
                self.assertRaisesRegex(UserError, "Otro usuario está enviando"),
            ):
                move.action_l10n_cl_dte_send()
            client_class.assert_not_called()
            self.assertEqual(move.l10n_cl_dte_status, "not_sent")

            first_cursor.rollback()
            move.action_l10n_cl_dte_send()

            self.assertEqual(move.l10n_cl_dte_status, "ask_for_status")
            self.assertEqual(move.l10n_cl_dte_track_id, f"DEMO-{move.id}-200")
        finally:
            first_cursor.close()

    def test_manual_reconciliation_requires_manager_valid_track_and_immutable_audit(
        self,
    ):
        move = self._uncertain_invoice()
        user = self.env["res.users"].create(
            {
                "name": "DTE operator without manager role",
                "login": "dte-operator-reconcile-test",
                "company_id": self.company.id,
                "company_ids": [(6, 0, [self.company.id])],
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            self.env.ref("base.group_user").id,
                            self.env.ref("account.group_account_invoice").id,
                            self.env.ref("l10n_cl_dte_sii.group_dte_user").id,
                        ],
                    )
                ],
            }
        )
        with self.assertRaises(UserError):
            move.with_user(user).action_l10n_cl_dte_reconcile_uncertain()

        wizard = self.env["l10n_cl.dte.reconciliation.wizard"].create(
            {
                "move_id": move.id,
                "resolution": "received",
                "track_id": "12345",
                "evidence": "El TrackID no cumple longitud oficial.",
            }
        )
        with self.assertRaises(ValidationError):
            wizard.action_confirm()
        self.assertEqual(move.l10n_cl_dte_status, "error")
        audit_model = self.env["l10n_cl.dte.reconciliation"]
        with self.assertRaises(AccessError):
            audit_model.create({"move_id": move.id})
        valid_wizard = self.env["l10n_cl.dte.reconciliation.wizard"].create(
            {
                "move_id": move.id,
                "resolution": "received",
                "track_id": "1234567890",
                "evidence": "Consulta manual documentada en el portal SII.",
            }
        )
        valid_wizard.action_confirm()
        audit = move.l10n_cl_dte_reconciliation_ids
        other_company = self.env["res.company"].create(
            {
                "name": "DTE Reconciliation Other Company",
                "currency_id": self.company.currency_id.id,
            }
        )
        foreign_manager = self.env["res.users"].create(
            {
                "name": "DTE Manager Other Company",
                "login": "dte-manager-reconciliation-other-company",
                "company_id": other_company.id,
                "company_ids": [(6, 0, [other_company.id])],
                "groups_id": [
                    (
                        6,
                        0,
                        [self.env.ref("l10n_cl_dte_sii.group_dte_manager").id],
                    )
                ],
            }
        )
        self.assertEqual(
            audit_model.with_user(foreign_manager).search_count(
                [("id", "=", audit.id)]
            ),
            0,
        )
        with self.assertRaises(AccessError):
            audit.write({"evidence": "Alterada"})
        with self.assertRaises(AccessError):
            audit.unlink()

    def test_dte_roles_and_company_scope(self):
        other_company = self.env["res.company"].create(
            {
                "name": "DTE Other Company",
                "currency_id": self.company.currency_id.id,
            }
        )
        counter = self.env["res.users"].create(
            {
                "name": "DTE Counter",
                "login": "dte-counter-role-test",
                "company_id": self.company.id,
                "company_ids": [(6, 0, [self.company.id, other_company.id])],
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            self.env.ref("base.group_user").id,
                            self.env.ref("account.group_account_invoice").id,
                            self.env.ref("l10n_cl_dte_sii.group_dte_user").id,
                        ],
                    )
                ],
            }
        )
        manager = self.env["res.users"].create(
            {
                "name": "DTE Manager",
                "login": "dte-manager-role-test",
                "company_id": self.company.id,
                "company_ids": [(6, 0, [self.company.id])],
                "groups_id": [
                    (6, 0, [self.env.ref("l10n_cl_dte_sii.group_dte_manager").id])
                ],
            }
        )
        internal_user = self.env["res.users"].create(
            {
                "name": "Internal User Without DTE",
                "login": "internal-without-dte-role-test",
                "company_id": self.company.id,
                "company_ids": [(6, 0, [self.company.id, other_company.id])],
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            }
        )
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": self.company_data["default_journal_sale"].id,
                "partner_id": self.partner_a.id,
                "invoice_date": "2026-10-28",
            }
        )
        move.with_user(counter)._check_dte_action_access()
        move.with_user(manager)._check_dte_action_access()
        with self.assertRaises(UserError):
            move.with_user(internal_user)._check_dte_action_access()
        with self.assertRaises(UserError):
            move.with_user(counter).with_context(
                allowed_company_ids=[other_company.id]
            )._check_dte_action_access()

        caf_model = self.env["l10n_cl.dte.caf"]
        self.assertTrue(caf_model.with_user(counter).has_access("read"))
        self.assertFalse(caf_model.with_user(counter).has_access("write"))
        self.assertTrue(caf_model.with_user(manager).has_access("write"))
        self.assertNotIn(
            "l10n_cl_dte_environment",
            self.env["res.company"]
            .with_user(counter)
            .fields_get(["l10n_cl_dte_environment"]),
        )
        self.assertIn(
            "l10n_cl_dte_environment",
            self.env["res.company"]
            .with_user(manager)
            .fields_get(["l10n_cl_dte_environment"]),
        )
        settings_model = self.env["l10n_cl.dte.company.settings"]
        settings = settings_model.with_user(manager).create(
            {"company_id": self.company.id}
        )
        self.assertEqual(settings.l10n_cl_dte_environment, "SIIDEMO")
        settings.write({"l10n_cl_dte_activity": "Giro de prueba actualizado"})
        settings.action_apply()
        self.assertEqual(
            self.company.l10n_cl_dte_activity, "Giro de prueba actualizado"
        )
        foreign_settings = settings_model.with_user(manager).create(
            {"company_id": other_company.id}
        )
        with self.assertRaises(UserError):
            foreign_settings.action_apply()
        with self.assertRaises(AccessError):
            settings_model.with_user(counter).create({"company_id": self.company.id})


@tagged("post_install", "-at_install")
class TestDteSendButtonTour(AccountTestInvoicingHttpCommon):
    @classmethod
    @AccountTestInvoicingHttpCommon.setup_country("cl")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.env.user.groups_id |= cls.env.ref("l10n_cl_dte_sii.group_dte_manager")
        cls.company.l10n_cl_dte_environment = "SIIDEMO"
        cls.partner_a.country_id = cls.company.country_id
        cls.partner_a.vat = "76543210-3"
        cls.partner_a.l10n_cl_sii_taxpayer_type = "1"

    def test_double_click_sends_one_rpc_from_invoice_form(self):
        journal = self.company_data["default_journal_sale"]
        document_type = self.env["l10n_latam.document.type"].search(
            [("country_id.code", "=", "CL"), ("code", "=", "33")], limit=1
        )
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": journal.id,
                "partner_id": self.partner_a.id,
                "invoice_date": "2026-10-28",
                "l10n_latam_document_type_id": document_type.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Servicio de prueba de doble clic",
                            "quantity": 1,
                            "price_unit": 1000,
                            "account_id": self.company_data[
                                "default_account_revenue"
                            ].id,
                        },
                    )
                ],
            }
        )
        move.with_context(l10n_cl_skip_dte_emission=True).action_post()
        envelope = self.env["ir.attachment"].create(
            {
                "name": "sobre-double-click.xml",
                "raw": b"<EnvioDTE/>",
                "mimetype": "application/xml",
                "res_model": "account.move",
                "res_id": move.id,
                "company_id": self.company.id,
            }
        )
        move._l10n_cl_write_dte_lifecycle_values(
            {
                "l10n_cl_dte_folio": 901,
                "l10n_cl_dte_envelope_attachment_id": envelope.id,
                "l10n_cl_dte_status": "not_sent",
            }
        )

        model_class = type(move)
        original_action = model_class.action_l10n_cl_dte_send
        calls = []

        def delayed_action(record):
            calls.append(record.id)
            time.sleep(1)
            return original_action(record)

        with patch.object(model_class, "action_l10n_cl_dte_send", delayed_action):
            self.start_tour(
                f"/web#id={move.id}&model=account.move&view_type=form",
                "l10n_cl_dte_send_double_click_tour",
                login=self.env.user.login,
            )

        self.assertEqual(calls, [move.id])
        self.assertEqual(move.l10n_cl_dte_status, "ask_for_status")
        self.assertEqual(move.l10n_cl_dte_track_id, f"DEMO-{move.id}-901")
