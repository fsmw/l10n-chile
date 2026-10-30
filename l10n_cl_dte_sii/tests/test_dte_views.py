# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from lxml import etree

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDteViews(TransactionCase):
    def test_outgoing_action_reuses_invoice_form_and_dte_search(self):
        action = self.env.ref("l10n_cl_dte_sii.action_dte_outgoing")
        self.assertEqual(action.res_model, "account.move")
        self.assertEqual(action.view_mode, "list,form")
        list_binding = self.env.ref("l10n_cl_dte_sii.action_dte_outgoing_list")
        self.assertEqual(list_binding.act_window_id, action)
        self.assertEqual(
            list_binding.view_id,
            self.env.ref("l10n_cl_dte_sii.view_out_invoice_list_dte_sii"),
        )
        rendered_list = self.env["account.move"].get_view(
            list_binding.view_id.id, "list"
        )["arch"]
        self.assertEqual(etree.fromstring(rendered_list).get("sample"), "0")
        for field_name in (
            "l10n_cl_dte_type_code",
            "l10n_latam_document_number",
            "l10n_cl_dte_status",
            "l10n_cl_dte_track_id",
        ):
            self.assertIn(field_name, rendered_list)
        arch = etree.fromstring(rendered_list)
        for field_name, label in (
            ("l10n_cl_dte_type_code", "Tipo DTE"),
            ("l10n_latam_document_number", "Folio"),
            ("l10n_cl_dte_track_id", "Track ID"),
        ):
            node = arch.xpath(f"//field[@name='{field_name}']")[0]
            self.assertEqual(node.get("string"), label)
            self.assertEqual(node.get("optional"), "show")
        self.assertEqual(
            action.search_view_id,
            self.env.ref("l10n_cl_dte_sii.view_account_invoice_filter_dte_sii"),
        )
        self.assertIn("l10n_cl_dte_status", action.domain)
        self.assertIn("group_dte_status", action.search_view_id.arch_db)

    def test_invoice_actions_and_company_configuration_are_present(self):
        invoice_view = self.env.ref("l10n_cl_dte_sii.view_move_form_dte_sii")
        for action in (
            "action_l10n_cl_dte_send",
            "action_l10n_cl_dte_check_status",
            "action_l10n_cl_dte_send_to_receiver",
            "action_l10n_cl_dte_open_xml",
            "action_l10n_cl_dte_open_envelope",
        ):
            self.assertIn(action, invoice_view.arch_db)
        invoice_arch = etree.fromstring(invoice_view.arch_db)
        for field_name, label in (
            ("l10n_cl_dte_type_code", "Tipo DTE"),
            ("l10n_latam_document_number", "Folio"),
            ("l10n_cl_dte_status", "Estado SII"),
            ("l10n_cl_dte_track_id", "Track ID"),
            (
                "l10n_cl_dte_receiver_delivery_status",
                "Entrega del DTE al receptor",
            ),
            ("l10n_cl_dte_last_status_check", "Última consulta SII"),
            ("l10n_cl_dte_last_status_error", "Error de la última consulta SII"),
            ("l10n_cl_dte_diagnostic", "Diagnóstico SII"),
            ("l10n_cl_dte_sii_status_code", "Código de estado SII"),
            ("l10n_cl_dte_sii_server_code", "Código de servicio SII"),
            ("l10n_cl_dte_sii_sql_code", "Código SQL SII"),
            ("l10n_cl_dte_sii_error_code", "Código de error SII"),
            ("l10n_cl_dte_sii_token_code", "Código de token SII"),
            ("l10n_cl_dte_sii_attention_number", "Número de atención SII"),
            ("l10n_cl_dte_sii_document_type", "Tipo de documento informado"),
            ("l10n_cl_dte_sii_informed_count", "Documentos informados"),
            ("l10n_cl_dte_sii_accepted_count", "Documentos aceptados"),
            ("l10n_cl_dte_sii_rejected_count", "Documentos rechazados"),
            ("l10n_cl_dte_sii_objection_count", "Documentos con reparos"),
        ):
            node = invoice_arch.xpath(f"//field[@name='{field_name}']")[0]
            self.assertEqual(node.get("string"), label)
        self.assertIn("DTE SII", invoice_view.arch_db)
        company_view = self.env.ref("l10n_cl_dte_sii.view_company_form_dte_sii")
        self.assertIn('name="country_code" invisible="1"', company_view.arch_db)
        self.assertIn("invisible=\"country_code != 'CL'\"", company_view.arch_db)
        for field_name in (
            "l10n_cl_dte_environment",
            "l10n_cl_dte_activity_code",
            "l10n_cl_dte_certificate_status",
            "l10n_cl_dte_resolution_number",
            "l10n_cl_dte_resolution_date",
        ):
            self.assertIn(field_name, company_view.arch_db)
        self.assertIn("Configurar emisor DTE", company_view.arch_db)
        settings_view = self.env.ref("l10n_cl_dte_sii.view_dte_company_settings_form")
        for field_name in (
            "l10n_cl_dte_environment",
            "l10n_cl_dte_activity",
            "certificate_option",
            "l10n_cl_dte_resolution_number",
        ):
            self.assertIn(field_name, settings_view.arch_db)
        settings_action = self.env.ref("l10n_cl_dte_sii.action_dte_company_settings")
        self.assertEqual(settings_action.res_model, "l10n_cl.dte.company.settings")
        self.assertEqual(settings_action.target, "new")
        self.assertTrue(self.env.ref("l10n_cl_dte_sii.menu_dte_company_settings"))
        journal_view = self.env.ref("l10n_cl_dte_sii.view_account_journal_form_dte_sii")
        self.assertIn('name="country_code" invisible="1"', journal_view.arch_db)
