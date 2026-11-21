# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from freezegun import freeze_time
from lxml import etree

from odoo.addons.l10n_cl_dte_gateway.services.claim_registry import (
    SiiClaimRegistryClient,
)
from odoo.addons.l10n_cl_dte_signing.services.signature_service import (
    C14N_URI,
    DS_NAMESPACE,
    RSA_SHA1_URI,
    SHA1_URI,
    _canonicalize,
)
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase

SII = "http://www.sii.cl/SiiDte"
RECEPTION = "2026-01-15 12:00:00"
OUR_VAT = "76864280-K"


def _sub(parent, tag, text=None, namespace=SII, **attrib):
    node = etree.SubElement(parent, f"{{{namespace}}}{tag}", **attrib)
    if text is not None:
        node.text = str(text)
    return node


def _b64_int(value):
    return base64.b64encode(value.to_bytes((value.bit_length() + 7) // 8, "big"))


class ReceiverEventsCase(DteInvoiceTestCase):
    """Synthetic issued DTEs accepted by SII and synthetic receiver answers."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company.with_env(cls.env)
        cls.company.sudo().write({"dte_sii_environment": "certification"})

    # Issued documents -------------------------------------------------------

    def issued_document(self, folio=501, code="33", with_move=True, at=RECEPTION):
        move = self.make_invoice(code=code) if with_move else False
        with freeze_time(at):
            values = {"document_code": code}
            if move:
                values["source_move_id"] = move.id
            document = self.create_document(**values)
            caf = self.create_artifact(document, "caf_source")
            document.action_record_transition(
                "authorized", "test_caf_validated", artifact_id=caf.id
            )
            document.action_record_transition(
                "numbered",
                "test_folio_reserved",
                artifact_id=caf.id,
                folio_number=folio,
            )
            self.put_document_in_queued_state(document)
            sent = self.create_artifact(document, "sii_response", b"<RESP/>")
            document.action_record_transition(
                "submitted", "test_submitted", artifact_id=sent.id
            )
            status = self.create_artifact(document, "status_response", b"<EST/>")
            document.action_record_transition(
                "accepted", "test_accepted", artifact_id=status.id
            )
        return document, move

    @staticmethod
    def registry_answer(*events):
        return {
            "code": 15,
            "description": "Listado de eventos del documento",
            "events": [
                {
                    "code": code,
                    "description": f"Synthetic {code}",
                    "responsible": "76234567-6",
                    "date": date,
                }
                for code, date in events
            ],
            "success": False,
            "raw_response": b"<synthetic/>",
        }

    @staticmethod
    def mock_registry(**kwargs):
        return patch.object(SiiClaimRegistryClient, "list_events", **kwargs)

    # Signed receiver responses ----------------------------------------------

    def sign_child(self, parent, target):
        """Append an XMLDSig over ``target`` signed with the synthetic test key."""
        digest = hashlib.sha1(_canonicalize(target)).digest()
        signature = etree.SubElement(
            parent, f"{{{DS_NAMESPACE}}}Signature", nsmap={None: DS_NAMESPACE}
        )
        signed_info = _sub(signature, "SignedInfo", namespace=DS_NAMESPACE)
        _sub(signed_info, "CanonicalizationMethod", namespace=DS_NAMESPACE).set(
            "Algorithm", C14N_URI
        )
        _sub(signed_info, "SignatureMethod", namespace=DS_NAMESPACE).set(
            "Algorithm", RSA_SHA1_URI
        )
        reference = _sub(signed_info, "Reference", namespace=DS_NAMESPACE)
        reference.set("URI", "#" + target.get("ID"))
        _sub(reference, "DigestMethod", namespace=DS_NAMESPACE).set(
            "Algorithm", SHA1_URI
        )
        _sub(reference, "DigestValue", base64.b64encode(digest).decode(), DS_NAMESPACE)
        value = self.caf_test_key.sign(
            _canonicalize(signed_info), padding.PKCS1v15(), hashes.SHA1()
        )
        _sub(
            signature, "SignatureValue", base64.b64encode(value).decode(), DS_NAMESPACE
        )
        numbers = self.caf_test_key.public_key().public_numbers()
        key_info = _sub(signature, "KeyInfo", namespace=DS_NAMESPACE)
        rsa_value = _sub(
            _sub(key_info, "KeyValue", namespace=DS_NAMESPACE),
            "RSAKeyValue",
            namespace=DS_NAMESPACE,
        )
        _sub(rsa_value, "Modulus", _b64_int(numbers.n).decode(), DS_NAMESPACE)
        _sub(rsa_value, "Exponent", _b64_int(numbers.e).decode(), DS_NAMESPACE)
        return signature

    @staticmethod
    def _dte_reference(parent, folio, code="33", type_tag="TipoDTE"):
        _sub(parent, type_tag, code)
        _sub(parent, "Folio", folio)
        _sub(parent, "FchEmis", "2026-01-15")
        _sub(parent, "RUTEmisor", OUR_VAT)
        _sub(parent, "RUTRecep", "76234567-6")
        _sub(parent, "MntTotal", "11900")

    def respuesta_dte(self, folio, state="0", recipient=OUR_VAT, reception=False):
        root = etree.Element(f"{{{SII}}}RespuestaDTE", nsmap={None: SII})
        root.set("version", "1.0")
        result = _sub(root, "Resultado", ID="RESP-SYNTH-1")
        cover = _sub(result, "Caratula", version="1.0")
        _sub(cover, "RutResponde", "76234567-6")
        _sub(cover, "RutRecibe", recipient)
        _sub(cover, "IdRespuesta", "1")
        _sub(cover, "NroDetalles", "1")
        _sub(cover, "TmstFirmaResp", "2026-01-17T09:30:00")
        if reception:
            envelope = _sub(result, "RecepcionEnvio")
            _sub(envelope, "EstadoRecepEnv", "0")
            line = _sub(envelope, "RecepcionDTE")
            self._dte_reference(line, folio)
            _sub(line, "EstadoRecepDTE", state)
            _sub(line, "RecepDTEGlosa", "DTE Recibido OK")
        else:
            line = _sub(result, "ResultadoDTE")
            self._dte_reference(line, folio)
            _sub(line, "CodEnvio", "1")
            _sub(line, "EstadoDTE", state)
            _sub(line, "EstadoDTEGlosa", f"Synthetic result {state}")
        self.sign_child(root, result)
        return etree.tostring(root, encoding="ISO-8859-1", xml_declaration=True)

    def envio_recibos(self, folio):
        root = etree.Element(f"{{{SII}}}EnvioRecibos", nsmap={None: SII})
        root.set("version", "1.0")
        receipts = _sub(root, "SetRecibos", ID="SETREC-SYNTH-1")
        cover = _sub(receipts, "Caratula", version="1.0")
        _sub(cover, "RutResponde", "76234567-6")
        _sub(cover, "RutRecibe", OUR_VAT)
        _sub(cover, "TmstFirmaEnv", "2026-01-18T10:00:00")
        receipt = _sub(receipts, "Recibo", version="1.0")
        document = _sub(receipt, "DocumentoRecibo", ID="REC-SYNTH-1")
        self._dte_reference(document, folio, type_tag="TipoDoc")
        _sub(document, "Recinto", "Bodega sintetica")
        _sub(document, "RutFirma", "12345678-5")
        _sub(document, "Declaracion", "Recibo sintetico de mercaderias")
        _sub(document, "TmstFirmaRecibo", "2026-01-18T09:45:00")
        self.sign_child(receipt, document)
        self.sign_child(root, receipts)
        return etree.tostring(root, encoding="ISO-8859-1", xml_declaration=True)
