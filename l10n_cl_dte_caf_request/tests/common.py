# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Synthetic SII web assistant doubles: no test opens a network connection."""

import base64
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

from odoo import fields

from odoo.addons.l10n_cl_dte_caf.tests.common import CafTestCase

from ..services.sii_caf_client import SiiCafClient
from ..services.sii_caf_web_client import LOGOUT_URL
from ..services.sii_caf_web_types import HttpResponse

# Synthetic RUTs with valid check digits; they do not identify anyone.
COMPANY_RUT = "76864280-K"
OTHER_COMPANY_RUT = "76111111-6"
SYNTHETIC_CERTIFICATE = b"synthetic-certificate-pem"
SYNTHETIC_KEY = b"synthetic-private-key-pem"
CERTIFICATION_IDK = "100"


def page(body):
    return HttpResponse(200, {}, body.encode("iso-8859-1"))


def form(action, values):
    controls = "".join(
        f'<input type="hidden" name="{name}" value="{value}">'
        for name, value in values.items()
    )
    return page(f'<html><body><form action="{action}">{controls}</form></body></html>')


def login():
    return page('<script>document.cookie="TOKEN=synthetic; path=/"</script>')


def allocation_pages(folio_start, folio_end, confirmation=None):
    """Login, request form, confirmation with the reserved range, download form."""
    return [
        login(),
        form("of_solicita_folios_dcto", {"STEP": "1"}),
        form(
            "of_genera_folio",
            {
                "FOLIO_INI": str(folio_start),
                "FOLIO_FIN": str(folio_end),
                "NOMUSU": "SYNTHETIC OPERATOR",
                **(confirmation or {}),
            },
        ),
        form("of_genera_archivo", {"STEP": "2"}),
    ]


BLOCKED_PAGE = page(
    "<html><body><h3>Solicitud de Folios</h3><p>No es posible autorizar nuevos "
    "folios: el contribuyente registra folios autorizados sin usar.</p>"
    "</body></html>"
)
REJECTION_PAGE = page(
    "<html><body><p>Usted no tiene permisos para solicitar folios.</p>" "</body></html>"
)


class FakeTransport:
    """Answer queued responses; the logout link always succeeds."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers, body=None, certificate=None, key=None):
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "body": body}
        )
        if url == LOGOUT_URL:
            return HttpResponse(200, {}, b"")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    @property
    def assistant_calls(self):
        return [call for call in self.calls if call["url"] != LOGOUT_URL]

    def close(self):
        return None


def synthetic_credentials(company, environment):
    return SYNTHETIC_CERTIFICATE, SYNTHETIC_KEY


class CafRequestTestCase(CafTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Each attempt state change commits on its own cursor; in tests those
        # cursors share the test transaction.
        cls.registry.enter_test_mode(cls.cr)
        cls.addClassCleanup(cls.registry.leave_test_mode)
        cls.company.partner_id.sudo().write(
            {"vat": COMPANY_RUT, "country_id": cls.env.ref("base.cl").id}
        )
        cls.company.sudo().write({"dte_sii_environment": "certification"})
        cls.Request = cls.env["l10n_cl.caf.request"]
        cls.Rule = cls.env["l10n_cl.caf.replenishment.rule"]

    @contextmanager
    def sii(self, *responses):
        """Route every request to a fake SII assistant; yield its transport."""
        transport = FakeTransport(responses)

        def get_client(request):
            return SiiCafClient(
                request.environment,
                transport_factory=lambda: transport,
                credentials=synthetic_credentials,
            )

        with patch.object(
            type(self.Request), "_get_caf_client", autospec=True, side_effect=get_client
        ):
            yield transport

    def caf_bytes(self, folio_start=1, folio_end=10, document_code="33", **values):
        return self.build_caf_xml(
            document_code=document_code,
            folio_start=folio_start,
            folio_end=folio_end,
            issuer_vat=values.get("issuer_vat", COMPANY_RUT),
            date_from=values.get("date_from"),
            key_id=values.get("key_id", CERTIFICATION_IDK),
        )

    def caf_response(self, folio_start=1, folio_end=10, document_code="33", **values):
        return HttpResponse(
            200, {}, self.caf_bytes(folio_start, folio_end, document_code, **values)
        )

    def success(self, folio_start=1, folio_end=10, document_code="33", **values):
        return [
            *allocation_pages(folio_start, folio_end),
            self.caf_response(folio_start, folio_end, document_code, **values),
        ]

    def future_caf(self, folio_start=1, folio_end=10):
        """A CAF the SII assistant accepts but ``l10n_cl_dte_caf`` refuses."""
        tomorrow = fields.Date.today() + timedelta(days=5)
        return self.success(folio_start, folio_end, date_from=tomorrow)

    def recovery_pages(self, folio_start, folio_end, document_code="33"):
        rut, dv = COMPANY_RUT.split("-")
        return [
            login(),
            form("rf_reobtencion2_folios", {"STEP": "1"}),
            form(
                "rf_reobtencion3_folios",
                {
                    "RUT_EMP": rut,
                    "DV_EMP": dv,
                    "COD_DOCTO": document_code,
                    "FOLIO_INI": str(folio_start),
                    "FOLIO_FIN": str(folio_end),
                },
            ),
            self.caf_response(folio_start, folio_end, document_code),
        ]

    def new_request(self, quantity=10, document_code="33", approve=True, **values):
        request = self.Request.with_user(self.dte_manager).create(
            {
                "document_code": document_code,
                "requested_quantity": quantity,
                "environment": "certification",
                **values,
            }
        )
        if approve:
            request.action_approve()
        return request

    def import_caf(self, folio_start, folio_end, document_code="33"):
        """Register and validate a CAF obtained outside this addon."""
        authorization = (
            self.env["l10n_cl.caf.authorization"]
            .with_user(self.dte_manager)
            .create(
                {
                    "name": f"Manual CAF {document_code} {folio_start}",
                    "company_id": self.company.id,
                    "sii_environment": "certification",
                    "document_code": document_code,
                    "issuer_vat": COMPANY_RUT,
                    "folio_start": folio_start,
                    "folio_end": folio_end,
                    "date_from": fields.Date.today(),
                    "caf_file": base64.b64encode(
                        self.caf_bytes(folio_start, folio_end, document_code)
                    ),
                }
            )
        )
        authorization.action_validate_authorization()
        return authorization
