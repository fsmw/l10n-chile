# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import DteCertificationTestCase


@tagged("post_install", "-at_install")
class TestCertificationIncomingExchange(DteCertificationTestCase):
    def test_incoming_document_links_to_information_exchange_stage(self):
        process = self.create_certification_process()
        stage = process.stage_ids.filtered(
            lambda item: item.code == "information_exchange"
        )
        document = self._import_incoming_dte()
        document._link_to_certification_stage(stage)
        self.assertEqual(document.certification_stage_id, stage)
        self.assertEqual(stage.incoming_document_ids, document)
        self.assertIn(
            "linked_to_certification_stage", document.event_ids.mapped("event_code")
        )

    def test_incoming_exchange_readiness_requires_verification_and_response(self):
        process = self.create_certification_process()
        stage = process.stage_ids.filtered(
            lambda item: item.code == "information_exchange"
        )
        document = self._import_incoming_dte()
        document._link_to_certification_stage(stage)
        diagnostics = stage._check_incoming_exchange_readiness()
        codes = {item["code"] for item in diagnostics}
        self.assertIn("incoming_document_cryptographic_verification_required", codes)
        self.assertIn("incoming_response_submission_required", codes)

    def test_document_cannot_be_linked_to_a_different_stage_kind(self):
        process = self.create_certification_process()
        stage = process.stage_ids.filtered(lambda item: item.code == "assigned_set")
        document = self._import_incoming_dte()
        with self.assertRaises(ValidationError):
            document._link_to_certification_stage(stage)

    def _import_incoming_dte(self):
        receiver = self.company.partner_id.vat.encode()
        xml = (
            b"<DTE xmlns='http://www.sii.cl/SiiDte'><Documento ID='D'><Encabezado>"
            b"<IdDoc><TipoDTE>33</TipoDTE><Folio>501</Folio>"
            b"<FchEmis>2026-10-15</FchEmis></IdDoc>"
            b"<Emisor><RUTEmisor>76000000-0</RUTEmisor></Emisor>"
            b"<Receptor><RUTRecep>" + receiver + b"</RUTRecep></Receptor>"
            b"</Encabezado></Documento></DTE>"
        )
        return (
            self.env["l10n_cl.incoming.dte"]
            .with_user(self.certification_manager)
            .with_company(self.company)
            ._import_xml(xml)
        )
