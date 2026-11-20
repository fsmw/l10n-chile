# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Fixed operator messages per acquisition error code.

Messages are constant texts: they never include SII payloads, tokens,
certificates or exception strings that could carry credentials.
"""

from odoo.tools.translate import LazyTranslate

_lt = LazyTranslate(__name__, default_lang="en_US")

ERROR_MESSAGES = {
    "SII_ENVIRONMENT_INVALID": _lt(
        "CAF acquisition requires the Certification or Production environment."
    ),
    "SII_DOCUMENT_TYPE_INVALID": _lt(
        "The DTE type is not eligible for CAF acquisition."
    ),
    "SII_QUANTITY_INVALID": _lt("The requested folio quantity is out of range."),
    "SII_RUT_INVALID": _lt("The company RUT is missing or invalid."),
    "SII_CERTIFICATE_INVALID": _lt(
        "No usable signing certificate with its private key is configured for "
        "this SII environment."
    ),
    "SII_AUTHENTICATION_FAILED": _lt(
        "The SII rejected the login with the company certificate."
    ),
    "SII_SESSION_LIMIT": _lt(
        "The SII reached its limit of authenticated sessions. Close an existing "
        "SII session or wait one hour before trying again."
    ),
    "SII_SESSION_EXPIRED": _lt(
        "The SII session expired before the folios were requested."
    ),
    "SII_TRANSPORT_UNAVAILABLE": _lt(
        "The server cannot open certificate-authenticated SII connections "
        "(PycURL with OpenSSL and libcurl 7.71 or later is required)."
    ),
    "SII_TRANSPORT_ERROR": _lt(
        "The SII could not be reached before the folios were requested. "
        "No folios were requested."
    ),
    "SII_ASSISTANT_ERROR": _lt(
        "The SII folio assistant answered an unexpected page before the folios "
        "were requested. No folios were requested."
    ),
    "SII_FOLIO_BLOCKED": _lt(
        "The SII refused new folios because previously authorized folios "
        "remain unused."
    ),
    "SII_QUANTITY_LIMIT": _lt(
        "The SII did not complete the request because the quantity exceeds its "
        "authorized maximum. No CAF was issued."
    ),
    "SII_HARD_REJECTION": _lt("The SII rejected the CAF request."),
    "SII_DISPATCH_UNCERTAIN": _lt(
        "The SII may have allocated the reserved folios but the CAF could not be "
        "downloaded or verified. Do not repeat the request: recover the CAF from "
        "the SII and reconcile this request."
    ),
    "SII_RECOVERY_FAILED": _lt(
        "The CAF of the reserved range could not be recovered from the SII. The "
        "request remains uncertain."
    ),
    "CAF_VALIDATION_FAILED": _lt(
        "The SII returned a CAF that failed validation. The folios may be "
        "allocated: review the stored evidence and reconcile this request."
    ),
    "INTERRUPTED_BEFORE_DISPATCH": _lt(
        "The acquisition was interrupted before the folios were requested."
    ),
    "UNEXPECTED_ERROR": _lt("An unexpected error stopped the CAF acquisition."),
}


def error_message(env, code):
    """Return the translated fixed message of an error code."""
    message = ERROR_MESSAGES.get(code, ERROR_MESSAGES["UNEXPECTED_ERROR"])
    return message._translate(env.lang or "en_US")
