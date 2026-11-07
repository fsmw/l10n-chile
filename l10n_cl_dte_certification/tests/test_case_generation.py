# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase

from ..services.case_generation_service import CaseGenerationService


@tagged("post_install", "-at_install")
class TestCertificationCaseGeneration(DteInvoiceTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        source = (
            b"SET TEST - NUMERO DE ATENCION: 321\n"
            b"CASO 321-1\nDOCUMENTO FACTURA ELECTRONICA\n"
            b"ITEM  CANTIDAD  PRECIO UNITARIO  UNIDAD MEDIDA\n"
            b"Servicio sintetico  2  1250  UN\n"
            b"CASO 321-2\nDOCUMENTO NOTA DE CREDITO ELECTRONICA\n"
            b"REFERENCIA CASO 321-1\nRAZON REFERENCIA: CORRIGE TEXTO\n"
            b"DICE: Actividad anterior\nDEBE DECIR: Actividad corregida\n"
        )
        cls.certification_set = cls.env[
            "l10n_cl.dte.certification.set"
        ]._create_from_import(
            cls.company, "synthetic-generation-source.txt", base64.b64encode(source)
        )
        cls.certification_set.action_parse_source()
        cls.certification_set.action_materialize_cases()
        cls.certification_set.action_interpret_cases()
        cls.execution = cls.env["l10n_cl.dte.certification.run"]._create_from_selection(
            cls.certification_set, cls.certification_set.case_ids
        )
        cls.journal = cls.env["account.journal"].search(
            [("company_id", "=", cls.company.id), ("type", "=", "sale")], limit=1
        )
        cls.journal.sudo().write({"l10n_latam_use_documents": True})
        cls.income_account = cls.env["account.account"].search(
            [
                ("company_ids", "in", [cls.company.id]),
                ("account_type", "=", "income"),
            ],
            limit=1,
        )

    def test_generates_linked_draft_without_posting_or_consuming_caf(self):
        folios_before = self.env["l10n_cl.caf.folio"].search_count([])
        created_lines = CaseGenerationService(self.env).generate(
            self.execution,
            journal=self.journal,
            vat_tax=self.tax_19,
            income_account=self.income_account,
            partner_by_line={
                self.execution.line_ids.filtered(
                    lambda item: item.document_code == "33"
                ).id: self.receiver
            },
        )
        line = created_lines.ensure_one()
        self.assertEqual(line.generation_state, "generated")
        self.assertEqual(self.execution.state, "partial")
        note_line = self.execution.line_ids.filtered(
            lambda item: item.document_code == "61"
        )
        self.assertEqual(note_line.generation_state, "unsupported")
        self.assertEqual(line.move_id.state, "draft")
        self.assertEqual(line.move_id.dte_certification_run_line_id, line)
        self.assertEqual(line.move_id.invoice_line_ids.quantity, 2)
        self.assertEqual(line.move_id.invoice_line_ids.price_unit, 1250)
        self.assertEqual(line.move_id.invoice_line_ids.tax_ids, self.tax_19)
        self.assertEqual(self.env["l10n_cl.caf.folio"].search_count([]), folios_before)
        with self.assertRaisesRegex(UserError, "cannot be posted"):
            line.move_id.action_post()

    def test_missing_partner_fails_before_any_draft_is_created(self):
        before = self.env["account.move"].search_count([])
        with self.assertRaisesRegex(ValidationError, "Choose a customer"):
            CaseGenerationService(self.env).generate(
                self.execution,
                journal=self.journal,
                vat_tax=self.tax_19,
                income_account=self.income_account,
                partner_by_line={},
            )
        self.assertEqual(self.env["account.move"].search_count([]), before)
        self.assertEqual(self.execution.state, "draft")
