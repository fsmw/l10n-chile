# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Errors, results and the form parser of the SII Certification CAF assistant.

They are shared with ``l10n_cl_dte_caf_request``, which owns the generic
SII CAF web assistant client.
"""

from odoo.addons.l10n_cl_dte_gateway.services.sii_caf_web_types import (  # noqa: F401
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
