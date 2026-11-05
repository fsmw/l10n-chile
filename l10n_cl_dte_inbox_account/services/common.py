# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation

from odoo.exceptions import ValidationError

GROUP = "l10n_cl_dte_inbox_account.group_inbox_account_user"
MANAGER = "l10n_cl_dte_inbox_account.group_inbox_account_manager"
BINDING = "l10n_cl.incoming.dte.account.binding"
MAPPING = "l10n_cl.incoming.dte.account.mapping"
VERSION = "incoming_account_v1"


def vat(value):
    return re.sub(r"[^0-9K]", "", (value or "").upper())


def canonical_folio(value):
    value = str(value or "").strip()
    return (
        str(int(value))
        if value.isascii() and value.isdigit() and int(value) > 0
        else ""
    )


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def decimal(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("Expected a finite decimal") from error
    if not result.is_finite():
        raise ValueError("Expected a finite decimal")
    return result


class IncomingAccountError(ValidationError):
    def __init__(self, diagnostic):
        self.diagnostic = diagnostic
        super().__init__(diagnostic["message"])


def diagnostic(
    env, code, ordinal=0, source_value="", odoo_value="", severity="blocker"
):
    messages = {
        "source_not_reviewed": env._("The incoming DTE must be reviewed first."),
        "source_validation_failed": env._(
            "The source must pass local schema, signature and TED validation."
        ),
        "source_digest_mismatch": env._(
            "The stored XML no longer matches its immutable digest."
        ),
        "identity_mismatch": env._(
            "The source fiscal identity does not match the company or stored evidence."
        ),
        "company_disabled": env._(
            "Configure an enabled Chilean CLP company, purchase journal and"
            " purchase taxes first."
        ),
        "environment_mismatch": env._(
            "The incoming environment differs from the active company environment."
        ),
        "unsupported_feature": env._(
            "The XML contains economics outside the supported 33/34 scope."
        ),
        "supplier_missing": env._(
            "Create the supplier with its valid Chilean RUT before preparing this bill."
        ),
        "supplier_ambiguous": env._(
            "More than one commercial supplier matches the issuer RUT."
        ),
        "supplier_archived": env._("The matching commercial supplier is archived."),
        "mapping_missing": env._("Choose an account for every fiscal detail."),
        "mapping_ambiguous": env._(
            "The item codes resolve to conflicting purchase mappings."
        ),
        "account_invalid": env._(
            "Choose an active invoice account belonging to this company."
        ),
        "tax_invalid": env._(
            "Choose a compatible excluded purchase VAT 19 or configured exempt tax."
        ),
        "uom_unresolved": env._(
            "Resolve the source unit explicitly with a compatible invoice unit."
        ),
        "amount_mismatch": env._(
            "The native bill differs from the signed source economics; no "
            "rounding adjustment is allowed."
        ),
        "duplicate_bill": env._(
            "Another bill or binding already reserves this supplier, "
            "document type and folio."
        ),
        "existing_bill_incompatible": env._(
            "Select a matching draft and associate every fiscal detail exactly once."
        ),
        "stale_preview": env._(
            "The source, choices or configuration changed. Refresh the "
            "preview before confirming."
        ),
        "payments_not_imported": env._(
            "Payment metadata in the XML will not create payments or reduce this bill."
        ),
    }
    return dict(
        code=code,
        severity=severity,
        ordinal=ordinal,
        source_value=str(source_value),
        odoo_value=str(odoo_value),
        message=messages[code],
    )


def reject(env, code, **kwargs):
    raise IncomingAccountError(diagnostic(env, code, **kwargs))


def source_snapshot(document):
    return {key: value for key, value in document.items() if key != "information"}
