# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json

from odoo import fields

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.services.invoice_snapshot import InvoiceSnapshotService
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import DteXmlComposer
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase

from .test_production_readiness import TestDteProductionReadiness


class ProductionRequestCase(DteInvoiceTestCase):
    """Real offline crypto with generated certificates and production-mode CAFs."""

    def setUp(self):
        super().setUp()
        self.company = self.company.with_user(self.dte_manager)
        TestDteProductionReadiness.configure_company_production(self)
        self.company.sudo().write({"dte_sii_environment": "production"})
        self.caf = TestDteProductionReadiness.create_production_caf(self)
        self.document = self.create_document()
        self.caf.action_reserve_folio(self.document, "request-binding-test")
        invoice = self.make_invoice(post=False)
        invoice.invoice_date = fields.Date.today()
        invoice.action_post()
        self.snapshot = InvoiceSnapshotService().capture_invoice(
            invoice, self.caf, 100, "33"
        )
        evidence = self.create_artifact(
            self.document, "source_snapshot", json.dumps(self.snapshot).encode()
        )
        self.document.action_record_source_snapshot(evidence.id)
        self.snapshot["ted_xml"] = DteXmlComposer().compose_ted(self.snapshot, self.caf)
        self.unsigned = DteXmlComposer().compose_unsigned_document(self.snapshot)
        evidence = self.create_artifact(self.document, "unsigned_xml", self.unsigned)
        self.document.action_record_transition(
            "prepared", "test_prepared", artifact_id=evidence.id
        )
        self.signed = SignatureService().sign_document(
            self.company, "production", self.unsigned
        )
        evidence = self.create_artifact(self.document, "signed_xml", self.signed)
        self.document.action_record_transition(
            "signed", "test_signed", artifact_id=evidence.id
        )
        self.original = self.create_artifact(self.document, "sii_request", self.signed)
        self.document.action_record_transition(
            "queued", "test_queued", artifact_id=self.original.id
        )
        self.submission = (
            self.env["l10n_cl.electronic.submission"]
            .sudo()
            .create(
                {
                    "document_id": self.document.id,
                    "idempotency_key": "request-binding-test",
                }
            )
        )
