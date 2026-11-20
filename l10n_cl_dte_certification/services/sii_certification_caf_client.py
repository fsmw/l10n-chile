# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""HTTP client for the SII Certification CAF assistant.

The generic web assistant client lives in ``l10n_cl_dte_caf_request``; this
module fixes it to the Certification environment (maullin).
"""

from odoo.addons.l10n_cl_dte_caf_request.services.sii_caf_web_client import (  # noqa: F401
    ASSISTANT_TIMEOUT,
    AUTH_URL,
    CONNECT_TIMEOUT,
    IDK,
    LOGIN_TIMEOUT,
    LOGOUT_TIMEOUT,
    LOGOUT_URL,
    MAX_REDIRECTS,
    PORTAL_URL,
    SESSION_URL,
    USER_AGENT,
    PycurlTransport,
    SiiCafSession,
    SiiCafWebClient,
)

from .sii_certification_caf_helpers import (  # noqa: F401
    MAX_RESPONSE_BYTES,
    _check_quantity_limit,
    _check_recovery_session,
    _check_session,
    _check_sii_error,
    _decode_html,
    _host,
    _normalize_vat,
    _parse_caf,
    _parse_form,
    _resolve_url,
    _split_vat,
    _validate_assistant_url,
    _validate_recovery_url,
    _validate_sii_host,
)
from .sii_certification_caf_types import (  # noqa: F401
    CafAllocationRejected,
    CafDispatchUncertain,
    CafRequestRejected,
    CafResult,
    CafSessionExpired,
    CafTransportError,
    CafTransportUnavailable,
    HttpResponse,
    _FormParser,
)


class SiiCertificationCafClient(SiiCafWebClient):
    """Implement the five-step SII assistant flow for Certification only."""

    def __init__(self, transport=None):
        super().__init__(transport=transport, environment="certification")
