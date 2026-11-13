# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Allowed CAF failure codes and their operator-facing diagnostics."""

from odoo import _


def _safe_code(code):
    allowed = {
        "pycurl_missing",
        "pycurl_blob_options_missing",
        "libcurl_runtime_unavailable",
        "libcurl_version_unsupported",
        "libcurl_openssl_backend_required",
        "certificate_login_rejected",
        "sii_session_limit_exceeded",
        "sii_session_expired",
        "folio_confirmation_missing",
        "folio_range_missing",
        "folio_range_unexpected",
        "sii_rejected_request",
        "sii_quantity_limit_exceeded",
        "redirect_outside_sii",
        "assistant_url_outside_certification",
        "caf_validation_after_dispatch",
        "caf_identity_or_range_mismatch",
        "caf_download_or_validation_uncertain",
        "dispatch_outcome_unknown",
        "worker_lost_after_dispatch_marker",
    }
    return code if code in allowed else "caf_request_failed"


def _diagnostic(code):
    messages = {
        "sii_quantity_limit_exceeded": _(
            "The requested quantity exceeds the maximum authorized by SII. "
            "Reduce the quantity before submitting; no folios were allocated."
        ),
        "certificate_login_rejected": _(
            "The SII did not accept the configured certificate. Verify its validity "
            "and representative authorization."
        ),
        "sii_session_expired": _(
            "The SII session expired before folio dispatch. A new explicit request "
            "may be created."
        ),
        "caf_validation_after_dispatch": _(
            "Folios may have been issued, but the returned CAF did not pass local "
            "validation. Recover it from Folios ya autorizados and reconcile it."
        ),
        "caf_identity_or_range_mismatch": _(
            "The returned CAF did not match the reserved folio range or company. "
            "Recover it from Folios ya autorizados and reconcile it."
        ),
        "caf_download_or_validation_uncertain": _(
            "Folios may have been issued, but the CAF download or validation failed. "
            "Recover it from Folios ya autorizados and reconcile it."
        ),
        "dispatch_outcome_unknown": _(
            "The SII outcome is unknown. Recover the CAF from Folios ya autorizados "
            "and reconcile it. Do not request this range again."
        ),
        "worker_lost_after_dispatch_marker": _(
            "The worker stopped after dispatch was prepared. Reconcile the range "
            "with Folios ya autorizados before taking further action."
        ),
        "pycurl_missing": _("PycURL is not installed in the Odoo runtime."),
        "pycurl_blob_options_missing": _(
            "The installed PycURL does not expose in-memory certificate options."
        ),
        "libcurl_runtime_unavailable": _(
            "The libcurl runtime could not be identified."
        ),
        "libcurl_version_unsupported": _(
            "libcurl 7.71.0 or newer is required for in-memory mTLS."
        ),
        "libcurl_openssl_backend_required": _(
            "The libcurl runtime must use the OpenSSL TLS backend."
        ),
        "redirect_outside_sii": _(
            "SII authentication attempted to leave the sii.cl domain."
        ),
        "assistant_url_outside_certification": _(
            "The SII assistant attempted to leave the Certification host."
        ),
        "caf_request_failed": _(
            "The CAF request could not continue. Review the certificate and retry "
            "only if the request did not reach folio dispatch."
        ),
    }
    return messages.get(code, messages["caf_request_failed"])
