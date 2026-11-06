# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib

from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_gateway.services.envelope_result_parser import (
    reconcile_result,
)
from odoo.addons.l10n_cl_dte_signing.tests.common import SigningTestCase


class TestEnvelopeResultImport(SigningTestCase):
    def setUp(self):
        super().setUp()
        self.company = self.company.with_user(self.dte_manager)
        self.company.sudo().write({"dte_sii_environment": "certification"})
        self.company.partner_id.sudo().write({"vat": "12345678-5"})
        self.envelope = b"""<EnvioDTE xmlns="http://www.sii.cl/SiiDte">
          <SetDTE>
            <Caratula><RutEmisor>12.345.678-5</RutEmisor>
              <RutEnvia>12.345.678-5</RutEnvia>
            </Caratula>
            <DTE><Documento><Encabezado><IdDoc><TipoDTE>33</TipoDTE>
              <Folio>101</Folio><FchEmis>2026-11-06</FchEmis></IdDoc>
              <Emisor><RUTEmisor>12.345.678-5</RUTEmisor></Emisor>
              <Receptor><RUTRecep>76.123.456-0</RUTRecep></Receptor>
              </Encabezado></Documento></DTE>
            <DTE><Documento><Encabezado><IdDoc><TipoDTE>33</TipoDTE>
              <Folio>102</Folio><FchEmis>2026-11-06</FchEmis></IdDoc>
              <Emisor><RUTEmisor>12.345.678-5</RUTEmisor></Emisor>
              <Receptor><RUTRecep>76.123.456-0</RUTRecep></Receptor>
              </Encabezado></Documento></DTE>
            <DTE><Documento><Encabezado><IdDoc><TipoDTE>56</TipoDTE>
              <Folio>201</Folio><FchEmis>2026-11-06</FchEmis></IdDoc>
              <Emisor><RUTEmisor>12.345.678-5</RUTEmisor></Emisor>
              <Receptor><RUTRecep>76.123.456-0</RUTRecep></Receptor>
              </Encabezado></Documento></DTE>
          </SetDTE>
        </EnvioDTE>"""
        self.track_id = "900000001"
        self.result_xml = b"""<RESULTADO_ENVIO><IDENTIFICACION>
          <RUTEMISOR>12345678-5</RUTEMISOR><RUTENVIA>12345678-5</RUTENVIA>
          <TRACKID>900000001</TRACKID>
          <TMSTRECEPCION>2026-11-06 12:00:00</TMSTRECEPCION>
          <ESTADO>EPR - Envio Procesado</ESTADO></IDENTIFICACION>
          <ESTADISTICA><SUBTOTAL><TIPODOC>33</TIPODOC><INFORMADO>2</INFORMADO>
            <ACEPTA>1</ACEPTA><RECHAZO>1</RECHAZO></SUBTOTAL>
            <SUBTOTAL><TIPODOC>56</TIPODOC><INFORMADO>1</INFORMADO>
              <ACEPTA>1</ACEPTA></SUBTOTAL></ESTADISTICA>
          <REVISIONENVIO><REVISIONDTE><FOLIO>102</FOLIO><TIPODTE>33</TIPODTE>
            <ESTADO>RCH - Rechazado</ESTADO><DETALLE>Synthetic error</DETALLE>
          </REVISIONDTE></REVISIONENVIO></RESULTADO_ENVIO>"""

    def make_submission(self):
        submission = (
            self.env["l10n_cl.sii.envelope.submission"]
            .with_user(self.dte_manager)
            ._create_from_workflow(
                {
                    "name": "Synthetic result test",
                    "company_id": self.company.id,
                    "environment": "certification",
                    "request_xml": base64.b64encode(self.envelope),
                    "request_filename": "synthetic-envelope.xml",
                    "request_digest": hashlib.sha256(self.envelope).hexdigest(),
                    "idempotency_key": "synthetic-result-import-test",
                }
            )
        )
        submission._write_internal(
            {"state": "result_pending", "track_id": self.track_id}
        )
        return submission

    def test_result_reconciles_by_document_identity_and_preserves_submission_state(
        self,
    ):
        submission = self.make_submission()
        wizard = (
            self.env["l10n_cl.sii.envelope.result.import.wizard"]
            .with_user(self.dte_manager)
            .create(
                {
                    "submission_id": submission.id,
                    "result_file": base64.b64encode(self.result_xml),
                    "result_filename": "DTEMAIL-synthetic.xml",
                }
            )
        )

        action = wizard.action_import()

        result = self.env["l10n_cl.sii.envelope.result.import"].browse(action["res_id"])
        self.assertEqual(result.reconciliation_state, "reconciled")
        self.assertEqual(result.verification_state, "operator_attestation")
        self.assertEqual(result.expected_count, 3)
        self.assertEqual(result.accepted_count, 2)
        self.assertEqual(result.rejected_count, 1)
        self.assertEqual(result.indeterminate_count, 0)
        self.assertEqual(result.attempt_id.operation, "result_import")
        self.assertEqual(result.attempt_id.response_filename, "DTEMAIL-synthetic.xml")
        self.assertEqual(result.source_xml, base64.b64encode(self.result_xml))
        self.assertEqual(submission.state, "result_pending")
        line = result.line_ids.filtered(
            lambda item: item.document_type == "33" and item.folio == "102"
        )
        self.assertEqual(line.state, "rejected")
        self.assertEqual(line.sii_state_code, "RCH")
        self.assertEqual(line.sii_detail, "Synthetic error")

        duplicate = (
            self.env["l10n_cl.sii.envelope.result.import.wizard"]
            .with_user(self.dte_manager)
            .create(
                {
                    "submission_id": submission.id,
                    "result_file": base64.b64encode(self.result_xml),
                    "result_filename": "DTEMAIL-synthetic.xml",
                }
            )
        )
        duplicate_action = duplicate.action_import()
        self.assertEqual(duplicate_action["res_id"], result.id)
        self.assertEqual(len(submission.attempt_ids), 1)

    def test_result_track_id_must_match_submission(self):
        with self.assertRaises(ValidationError):
            reconcile_result(
                self.result_xml,
                self.envelope,
                self.company.vat,
                "9999999999",
            )

    def test_inconsistent_aggregate_marks_documents_indeterminate(self):
        response = self.result_xml.replace(b"<INFORMADO>2", b"<INFORMADO>3")

        result = reconcile_result(
            response, self.envelope, self.company.vat, self.track_id
        )

        self.assertEqual(result["reconciliation_state"], "mismatch")
        self.assertEqual(result["indeterminate_count"], 2)
        self.assertEqual(result["accepted_count"], 1)
