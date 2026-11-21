# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Synthetic accepted invoices and a stubbed SII RTC host for factoring tests."""

import base64
import json
from contextlib import contextmanager
from unittest.mock import patch

import requests

from odoo import fields

from odoo.addons.l10n_cl_dte_gateway.tests.test_production_readiness import (
    TestDteProductionReadiness,
)
from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.services.invoice_snapshot import InvoiceSnapshotService
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import DteXmlComposer
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase

TRANSPORT = "odoo.addons.l10n_cl_dte_factoring.services.rtc_transport"
ASSIGNMENT = "odoo.addons.l10n_cl_dte_factoring.models.dte_assignment.DteAssignment"


def upload_receipt(status="0", track_id="0000038936", company="12345678-5"):
    track = f"<TRACKID>{track_id}</TRACKID>" if track_id else ""
    return (
        f'<?xml version="1.0"?><RECEPCIONAEC><RUTSENDER>12345678-5</RUTSENDER>'
        f"<RUTCOMPANY>{company}</RUTCOMPANY><FILE>AEC.xml</FILE>"
        f"<TIMESTAMP>21-11-2026 10:00:00</TIMESTAMP><STATUS>{status}</STATUS>"
        f"{track}</RECEPCIONAEC>"
    ).encode()


def soap_answer(operation, payload):
    escaped = payload.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        '<?xml version="1.0" encoding="UTF-8"?><soapenv:Envelope '
        'xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"><soapenv:Body>'
        f'<ns1:{operation}Response xmlns:ns1="http://DefaultNamespace">'
        f"<{operation}Return>{escaped}</{operation}Return>"
        f"</ns1:{operation}Response></soapenv:Body></soapenv:Envelope>"
    ).encode()


def upload_status(state="EOK", code="0", track_id="38936"):
    return soap_answer(
        "getEstEnvio",
        '<?xml version="1.0" encoding="UTF-8"?><SII:RESPUESTA '
        'xmlns:SII="http://www.sii.cl/XMLSchema"><SII:RESP_HDR>'
        f"<SII:ESTADO>{code}</SII:ESTADO></SII:RESP_HDR><SII:RESP_BODY>"
        f"<TRACKID>{track_id}</TRACKID><ESTADO_ENVIO>{state}</ESTADO_ENVIO>"
        "<DESC_ESTADO>Synthetic state</DESC_ESTADO></SII:RESP_BODY></SII:RESPUESTA>",
    )


def assignment_registry(code="0", glosa="Documento Cedido"):
    return soap_answer(
        "getEstCesion",
        '<?xml version="1.0" encoding="UTF-8"?><SII:RESPUESTA '
        'xmlns:SII="http://www.sii.cl/XMLSchema"><SII:RESP_HDR>'
        f"<SII:ESTADO>{code}</SII:ESTADO><SII:GLOSA>{glosa}</SII:GLOSA>"
        "</SII:RESP_HDR><SII:RESP_BODY/></SII:RESPUESTA>",
    )


class FakeResponse:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_content(self, _size):
        yield self.content


class FakeSession:
    """Answer each POST with the next scripted reply; record every request."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return FakeResponse(reply)


class FakeAuthentication:
    """Stand-in for the gateway seed/token client; no network, no key use."""

    base_url = "https://palena.sii.cl"

    def __init__(self, session):
        self.session = session
        self.tokens = ["TOKEN1", "TOKEN2"]
        self.invalidated = 0
        self.closed = 0

    def token(self, company):
        return self.tokens[min(self.invalidated, 1)]

    def invalidate_token(self, company):
        self.invalidated += 1

    def close(self):
        self.closed += 1


class FactoringTestCase(DteInvoiceTestCase):
    """Production-mode company with a real signed and accepted DTE 33."""

    def setUp(self):
        super().setUp()
        self.company = self.company.with_user(self.dte_manager)
        TestDteProductionReadiness.configure_company_production(self)
        self.company.sudo().write({"dte_sii_environment": "production"})
        self.company.partner_id.sudo().write({"email": "cedente@example.com"})
        self.caf = TestDteProductionReadiness.create_production_caf(self)
        self.factor = (
            self.env["res.partner"]
            .sudo()
            .create(
                {
                    "name": "Factoring Sintetico SpA",
                    "vat": "76086428-5",
                    "street": "Avenida Factoring 300",
                    "city": "Santiago",
                    "email": "operaciones@factoring.example.com",
                    "country_id": self.chile.id,
                }
            )
        )
        self.invoice, self.document, self.signed_dte = self.accepted_invoice()

    def accepted_invoice(self, fiscal_state="accepted", folio=100):
        invoice = self.make_invoice(post=False)
        invoice.invoice_date = fields.Date.today()
        invoice.action_post()
        document = self.create_document(source_move_id=invoice.id)
        self.caf.action_reserve_folio(document, f"aec-test-{folio}-{invoice.id}")
        snapshot = InvoiceSnapshotService().capture_invoice(
            invoice, self.caf, folio, "33"
        )
        evidence = self.create_artifact(
            document, "source_snapshot", json.dumps(snapshot).encode()
        )
        document.action_record_source_snapshot(evidence.id)
        snapshot["ted_xml"] = DteXmlComposer().compose_ted(snapshot, self.caf)
        unsigned = DteXmlComposer().compose_unsigned_document(snapshot)
        evidence = self.create_artifact(document, "unsigned_xml", unsigned)
        document.action_record_transition(
            "prepared", "test_prepared", artifact_id=evidence.id
        )
        signed = SignatureService().sign_document(self.company, "production", unsigned)
        evidence = self.create_artifact(document, "signed_xml", signed)
        document.action_record_transition(
            "signed", "test_signed", artifact_id=evidence.id
        )
        request = self.create_artifact(document, "sii_request", signed)
        document.action_record_transition(
            "queued", "test_queued", artifact_id=request.id
        )
        if fiscal_state in {"submitted", "accepted"}:
            receipt = self.create_artifact(document, "sii_response", b"<receipt/>")
            document.action_record_transition(
                "submitted", "test_submitted", artifact_id=receipt.id
            )
        if fiscal_state == "accepted":
            status = self.create_artifact(document, "status_response", b"<status/>")
            document.action_record_transition(
                "accepted", "test_accepted", artifact_id=status.id
            )
        return invoice, document, signed

    def new_assignment(self, invoice=None, user=None):
        return (
            self.env["l10n_cl.dte.assignment"]
            .with_user(user or self.dte_manager)
            .with_company(self.company)
            .create(
                {"move_id": (invoice or self.invoice).id, "partner_id": self.factor.id}
            )
        )

    def signed_assignment(self):
        assignment = self.new_assignment()
        with patch(ASSIGNMENT + "._aec_timestamp", return_value="2026-11-21T10:00:00"):
            assignment.action_build_aec()
        return assignment

    @staticmethod
    def aec_bytes(assignment):
        return base64.b64decode(
            assignment.aec_evidence_id.sudo().with_context(bin_size=False).content
        )

    @contextmanager
    def rtc(self, *replies):
        """Route every RTC request through a scripted fake SII host."""
        session = FakeSession(replies)
        auth = FakeAuthentication(session)
        with patch.dict(
            TRANSPORT + ".AUTHENTICATIONS",
            {"production": lambda: auth, "certification": lambda: auth},
        ):
            yield auth

    @staticmethod
    def connection_lost():
        return requests.ConnectionError("synthetic connection reset")
