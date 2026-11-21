# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from lxml import etree

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from ..services.exchange_composer import RECEIPT_DECLARATION
from ..services.exchange_signer import (
    validate_exchange_schema,
    verify_exchange_signatures,
)
from .common import RECIPIENT_VAT, SUPPLIER_VAT, ExchangeCase

R = "./s:Resultado"


@tagged("post_install", "-at_install")
class TestExchangeDocuments(ExchangeCase):
    def test_reception_answers_envelope_and_each_dte(self):
        batch, _documents = self.receive(810, 820)
        exchange = self.exchanges._prepare_reception(batch)
        root = self.assert_valid_evidence(exchange, 1)
        self.assertEqual(etree.QName(root).localname, "RespuestaDTE")
        reception = R + "/s:RecepcionEnvio"
        self.assertEqual(
            self.text(root, R + "/s:Caratula/s:RutResponde"), [RECIPIENT_VAT]
        )
        self.assertEqual(self.text(root, R + "/s:Caratula/s:RutRecibe"), [SUPPLIER_VAT])
        self.assertEqual(self.text(root, reception + "/s:EstadoRecepEnv"), ["0"])
        self.assertEqual(
            self.text(root, reception + "/s:EnvioDTEID"), ["exchange-test-set"]
        )
        self.assertEqual(self.text(root, reception + "/s:CodEnvio"), [str(batch.id)])
        self.assertEqual(self.text(root, reception + "/s:NroDTE"), ["2"])
        self.assertTrue(self.text(root, reception + "/s:Digest")[0])
        self.assertEqual(
            self.text(root, reception + "/s:RecepcionDTE/s:EstadoRecepDTE"), ["0", "0"]
        )
        self.assertEqual(
            sorted(self.text(root, reception + "/s:RecepcionDTE/s:Folio")),
            ["810", "820"],
        )
        self.assertEqual(exchange.kind, "reception")
        self.assertEqual(exchange.import_id, batch)
        self.assertEqual(exchange.signer_vat, "12345678-5")
        self.assertEqual(exchange.state, "signed")
        parsed = self.parse_as_receiver_response(self.xml_of(exchange))
        if parsed is not None:
            self.assertEqual(parsed["kind"], "respuesta_dte")
            self.assertEqual(parsed["recipient_vat"], SUPPLIER_VAT)
            self.assertEqual(
                {item["code"] for item in parsed["items"]}, {"RECEPTION_OK"}
            )

    def test_reception_reports_dte_for_another_receiver(self):
        other = self.env["res.partner"].create(
            {
                "name": "Another receiver",
                "vat": "11111111-1",
                "country_id": self.chile.id,
                "street": "Calle Otra 1",
                "city": "Santiago",
            }
        )
        content = self.envelope(self.signed_dte(830, partner=other))
        batch = self.imports._import_xml(content, filename="EnvioDTE_wrong.xml")
        self.assertEqual(batch.state, "invalid")
        exchange = self.exchanges._prepare_reception(batch)
        root = self.assert_valid_evidence(exchange, 1)
        reception = R + "/s:RecepcionEnvio"
        self.assertEqual(self.text(root, reception + "/s:EstadoRecepEnv"), ["99"])
        self.assertEqual(
            self.text(root, reception + "/s:RecepcionDTE/s:EstadoRecepDTE"), ["3"]
        )
        self.assertFalse(exchange.line_ids.incoming_document_id)
        self.assertEqual(exchange.line_ids.status_code, "3")

    def test_commercial_results_accepted_objected_and_rejected(self):
        _batch, documents = self.receive(840, 850, 860)
        expected = {
            "0": "RESULT_ACCEPTED",
            "1": "RESULT_OBJECTIONS",
            "2": "RESULT_REJECTED",
        }
        for document, state in zip(
            documents.sorted("folio"), ("0", "1", "2"), strict=True
        ):
            exchange = self.exchanges._prepare_documents(
                "result", document, state=state, gloss=f"Synthetic reason {state}"
            )
            root = self.assert_valid_evidence(exchange, 1)
            self.assertEqual(
                self.text(root, R + "/s:ResultadoDTE/s:EstadoDTE"), [state]
            )
            self.assertEqual(
                self.text(root, R + "/s:ResultadoDTE/s:EstadoDTEGlosa"),
                [f"Synthetic reason {state}"],
            )
            self.assertEqual(
                self.text(root, R + "/s:ResultadoDTE/s:Folio"), [str(document.folio)]
            )
            self.assertEqual(exchange.line_ids.incoming_document_id, document)
            parsed = self.parse_as_receiver_response(self.xml_of(exchange))
            if parsed is not None:
                self.assertEqual(
                    [item["code"] for item in parsed["items"]], [expected[state]]
                )

    def test_one_result_covers_several_dtes_of_one_supplier(self):
        _batch, documents = self.receive(870, 880)
        exchange = self.exchanges._prepare_documents("result", documents)
        root = self.assert_valid_evidence(exchange, 1)
        self.assertEqual(self.text(root, R + "/s:Caratula/s:NroDetalles"), ["2"])
        self.assertEqual(
            self.text(root, R + "/s:ResultadoDTE/s:EstadoDTEGlosa"),
            ["DTE Aceptado OK", "DTE Aceptado OK"],
        )
        self.assertEqual(exchange.line_ids.incoming_document_id, documents)

    def test_goods_receipts_sign_each_receipt_and_the_set(self):
        _batch, documents = self.receive(890, 900)
        exchange = self.exchanges._prepare_documents(
            "receipt", documents, site="Bodega sintetica"
        )
        root = self.assert_valid_evidence(exchange, 3)
        document = "./s:SetRecibos/s:Recibo/s:DocumentoRecibo"
        self.assertEqual(etree.QName(root).localname, "EnvioRecibos")
        self.assertEqual(
            self.text(root, document + "/s:Recinto"), ["Bodega sintetica"] * 2
        )
        self.assertEqual(self.text(root, document + "/s:RutFirma"), ["12345678-5"] * 2)
        self.assertEqual(
            self.text(root, document + "/s:Declaracion"), [RECEIPT_DECLARATION] * 2
        )
        self.assertEqual(
            self.text(root, "./s:SetRecibos/s:Caratula/s:RutRecibe"), [SUPPLIER_VAT]
        )
        parsed = self.parse_as_receiver_response(self.xml_of(exchange))
        if parsed is not None:
            self.assertEqual(parsed["kind"], "envio_recibos")
            self.assertEqual(
                [item["code"] for item in parsed["items"]], ["GOODS_RECEIPT"] * 2
            )

    def test_tampered_response_fails_signature_and_schema(self):
        _batch, documents = self.receive(910)
        content = self.xml_of(
            self.exchanges._prepare_documents("receipt", documents, site="Bodega")
        )
        with self.assertRaises(ValidationError):
            verify_exchange_signatures(content.replace(b"Bodega", b"Patio"))
        with self.assertRaises(ValidationError):
            validate_exchange_schema(
                content.replace(b"<Recinto>Bodega", b"<Recinto>" + b"x" * 81)
            )

    def test_unreviewed_and_demo_documents_are_not_answered(self):
        batch = self.imports._import_xml(self.envelope(self.signed_dte(920)))
        document = batch.line_ids.incoming_document_id
        with self.assertRaises(UserError):
            self.exchanges._prepare_documents("result", document)
        self.recipient.sudo().dte_sii_environment = "demo"
        demo_batch = self.imports._import_xml(self.envelope(self.signed_dte(930)))
        with self.assertRaises(UserError):
            self.exchanges._prepare_reception(demo_batch)
        self.assertFalse(self.exchanges.search([]))
