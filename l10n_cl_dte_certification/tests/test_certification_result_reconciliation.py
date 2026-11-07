# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase

from ..services.case_generation_service import CaseGenerationService


@tagged("post_install", "-at_install")
class TestCertificationResultReconciliation(DteInvoiceTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        source = (
            b"SET TEST - NUMERO DE ATENCION: 321\n"
            b"CASO 321-1\nDOCUMENTO FACTURA ELECTRONICA\n"
            b"ITEM  CANTIDAD  PRECIO UNITARIO  UNIDAD MEDIDA\n"
            b"Servicio sintetico  2  1250  UN\n"
        )
        certification_set = cls.env[
            "l10n_cl.dte.certification.set"
        ]._create_from_import(
            cls.company, "synthetic-result-source.txt", base64.b64encode(source)
        )
        certification_set.action_parse_source()
        certification_set.action_materialize_cases()
        certification_set.action_interpret_cases()
        cls.execution = cls.env["l10n_cl.dte.certification.run"]._create_from_selection(
            certification_set, certification_set.case_ids
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

    def test_reconciles_operator_import_to_case_without_changing_dte(self):
        self.company.sudo().write({"dte_sii_environment": "certification"})
        run_line = self.execution.line_ids.ensure_one()
        CaseGenerationService(self.env).generate(
            self.execution,
            journal=self.journal,
            vat_tax=self.tax_19,
            income_account=self.income_account,
            partner_by_line={run_line.id: self.receiver},
            selected_lines=run_line,
        )
        caf = self.validate_authorization(
            document_code="33", folio_start=940, folio_end=941
        )
        self.create_signing_profile()
        adapter = run_line.move_id._get_dte_issuance_adapter("33")
        # Synthetic fixtures are Demo-only; keep the real CAF and signer guards
        # intact while exercising the certification case-to-result mapping.
        with (
            patch.object(type(adapter), "select_caf", return_value=caf),
            patch.object(type(caf), "_check_document_environment"),
            patch.object(
                type(adapter),
                "sign_document",
                side_effect=lambda company, _environment, xml: (
                    SignatureService().sign_document(company, "demo", xml)
                ),
            ),
        ):
            self.execution.action_prepare_dte()
        run_line.invalidate_recordset()
        self.assertEqual(
            run_line.preparation_state, "prepared", run_line.preparation_message
        )
        move_state = run_line.move_id.state

        envelope = b"synthetic envelope bytes for linkage"
        self.execution._record_exported_envelope(envelope)
        submission = self.env["l10n_cl.sii.envelope.submission"]._create_from_workflow(
            {
                "name": "Synthetic certification result",
                "company_id": self.company.id,
                "environment": "certification",
                "request_xml": base64.b64encode(envelope),
                "request_filename": "synthetic-envelope.xml",
                "request_digest": self.execution.envelope_digest,
                "idempotency_key": "synthetic-certification-result-success",
            }
        )
        submission._write_internal(
            {
                "state": "submitted",
                "track_id": "123456789",
                "submitted_at": fields.Datetime.now(),
            }
        )
        self.execution._record_sii_submission(submission)

        response = b"synthetic operator supplied result"
        response_digest = hashlib.sha256(response).hexdigest()
        attempt = self.env["l10n_cl.sii.envelope.attempt"]._create_for_operation(
            submission, "result_import", request_digest=response_digest
        )
        attempt._complete("completed", response, response_filename="synthetic.xml")
        imported = self.env["l10n_cl.sii.envelope.result.import"]._create_from_wizard(
            {
                "submission_id": submission.id,
                "attempt_id": attempt.id,
                "imported_at": fields.Datetime.now(),
                "imported_by_id": self.env.user.id,
                "source_digest": response_digest,
                "source_filename": "synthetic.xml",
                "verification_state": "operator_attestation",
                "reconciliation_state": "reconciled",
                "sii_state": "EPR",
                "expected_count": 1,
                "accepted_count": 0,
                "rejected_count": 1,
                "repair_count": 0,
                "indeterminate_count": 0,
            },
            [
                {
                    "issuer_vat": self.company.partner_id.vat,
                    "receiver_vat": self.receiver.vat,
                    "document_type": "33",
                    "folio": str(run_line.document_id.folio),
                    "document_digest": "synthetic-dte-digest",
                    "state": "rejected",
                    "sii_state_code": "RCH",
                    "sii_detail": "Synthetic rejection detail",
                }
            ],
        )

        action = self.execution.action_reconcile_sii_result()
        result = self.env["l10n_cl.dte.certification.run.result"].browse(
            action["res_id"]
        )
        result_line = result.line_ids.ensure_one()
        self.assertEqual(result.result_import_id, imported)
        self.assertEqual(result.verification_state, "operator_attestation")
        self.assertEqual(result_line.run_line_id, run_line)
        self.assertEqual(result_line.state, "rejected")
        self.assertEqual(result_line.sii_state_code, "RCH")
        self.assertEqual(run_line.move_id.state, move_state)
        self.assertEqual(
            self.execution.action_reconcile_sii_result()["res_id"], result.id
        )
