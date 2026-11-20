# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Parsing and validation helpers for the SII Certification CAF assistant.

The helpers live in ``l10n_cl_dte_caf_request``; their host-bound validators
default to the Certification host (maullin).
"""

from odoo.addons.l10n_cl_dte_gateway.services.sii_caf_web_helpers import (  # noqa: F401
    ERROR_MARKERS,
    LOGIN_MARKERS,
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
