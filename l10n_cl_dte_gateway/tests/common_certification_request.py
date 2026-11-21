# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Synthetic Certification (maullin) setup and recorded SII answers."""

import base64
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from odoo import fields

from ..services import certification_auth
from .test_certification_transport import soap_response, upload_response

SYNTHETIC_VAT = "12345678-5"


def configure_company_certification(case):
    """Prepare issuer, sender, resolution and one Certification signing profile."""
    case.company.partner_id.sudo().write({"vat": SYNTHETIC_VAT})
    case.company.sudo().write(
        {
            "dte_sii_sender_vat": SYNTHETIC_VAT,
            "dte_sii_resolution_number": "80",
            "dte_sii_resolution_date": fields.Date.today(),
        }
    )
    profile = case.create_signing_profile(
        company=case.company, environment="certification"
    )
    case.company.sudo().write({"dte_sii_environment": "certification"})
    return profile


def create_sii_caf(case, environment, code="33", folio_start=100):
    """Create a validated non-synthetic CAF for one real SII environment."""
    authorization = (
        case.env["l10n_cl.caf.authorization"]
        .with_user(case.dte_manager)
        .with_company(case.company)
        .create(
            {
                "name": f"Synthetic {environment} CAF {code}",
                "company_id": case.company.id,
                "sii_environment": environment,
                "is_synthetic": False,
                "document_code": code,
                "issuer_vat": case.company.partner_id.vat,
                "folio_start": folio_start,
                "folio_end": folio_start + 2,
                "date_from": fields.Date.today(),
                "caf_file": base64.b64encode(
                    case.build_caf_xml(
                        document_code=code,
                        folio_start=folio_start,
                        folio_end=folio_start + 2,
                        issuer_vat=case.company.partner_id.vat,
                    )
                ),
            }
        )
    )
    authorization.action_validate_authorization()
    return authorization


def dte_status_response(
    code="33", accepted=1, rejected=0, observed=0, track="123", state="EPR"
):
    """Return a QueryEstUp answer with one document-type counter row."""
    payload = (
        f"<SII:RESPUESTA xmlns:SII='http://www.sii.cl/XMLSchema'><SII:RESP_HDR>"
        f"<TRACKID>{track}</TRACKID><ESTADO>{state}</ESTADO>"
        f"<GLOSA>Synthetic answer</GLOSA></SII:RESP_HDR>"
        f"<SII:RESP_BODY><TIPO_DOCTO>{code}</TIPO_DOCTO>"
        f"<INFORMADOS>{accepted + rejected + observed}</INFORMADOS>"
        f"<ACEPTADOS>{accepted}</ACEPTADOS><RECHAZADOS>{rejected}</RECHAZADOS>"
        f"<REPAROS>{observed}</REPAROS></SII:RESP_BODY></SII:RESPUESTA>"
    )
    return soap_response("getEstUp", payload)


class FakeSiiSession:
    """Answer SII HTTP calls by path; never opens a network connection."""

    def __init__(self, upload=None, statuses=(), seed_state="00"):
        self.upload = upload if upload is not None else upload_response()
        self.statuses = list(statuses)
        self.seed_state = seed_state
        self.urls = []
        self.uploads = []
        self.trust_env = True
        self.headers = {}
        self.cookies = MagicMock()

    def _content(self, path, kwargs):
        if path.endswith("CrSeed.jws"):
            return soap_response(
                "getSeed",
                f"<R><ESTADO>{self.seed_state}</ESTADO><SEMILLA>004200</SEMILLA></R>",
            )
        if path.endswith("GetTokenFromSeed.jws"):
            return soap_response(
                "getToken", "<R><ESTADO>00</ESTADO><TOKEN>synthetic42</TOKEN></R>"
            )
        if path.endswith("DTEUpload"):
            self.uploads.append(kwargs["files"]["archivo"][1])
            if isinstance(self.upload, Exception):
                raise self.upload
            return self.upload
        return self.statuses.pop(0)

    def post(self, url, **kwargs):
        self.urls.append(url)
        content = self._content(url, kwargs)
        reply = MagicMock()
        reply.__enter__.return_value.status_code = 200
        reply.__enter__.return_value.iter_content.return_value = [content]
        return reply

    def close(self):
        return None

    def upload_count(self):
        return len(self.uploads)


@contextmanager
def fake_sii(session):
    """Route every SII authentication client to ``session``."""
    certification_auth._TOKEN_CACHE.clear()
    with patch.object(certification_auth.requests, "Session", return_value=session):
        yield session
    certification_auth._TOKEN_CACHE.clear()
