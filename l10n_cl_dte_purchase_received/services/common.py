# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.addons.l10n_cl_dte_inbox_account.services.common import (
    IncomingAccountError,
    decimal,
    diagnostic,
)
from odoo.addons.l10n_cl_dte_xml.services import retention_codes

# A purchase invoice the buyer issued to us documents our own sale.
PURCHASE_CODE = "46"
SALE_MOVE_TYPE = "out_invoice"

# SII ImptoReten code for VAT retained in full by the buyer, and its rate.
TOTAL_RETENTION_CODE = str(retention_codes.TOTAL_RETENTION_CODE)
TOTAL_RETENTION_RATE = retention_codes.VAT_RETENTION_RATES[
    retention_codes.TOTAL_RETENTION_CODE
]
VAT_RATE = retention_codes.VAT_RATE
VAT_SII_CODE = 14

# Product VAT retention codes (legumes, cattle, wood, ...) and the official
# rate of those this bridge books with a configured sale-side tax.
PRODUCT_RETENTION_CODES = frozenset(
    str(code) for code in retention_codes.PRODUCT_RETENTION_CODE_RANGE
)
PARTIAL_RETENTION_RATES = {
    str(code): rate
    for code, rate in retention_codes.VAT_RETENTION_RATES.items()
    if code != retention_codes.TOTAL_RETENTION_CODE
}


def received_messages(env):
    return {
        "received46_not_configured": env._(
            "Configure the DTE 46 sale journal, the sales VAT 19% (SII code 14)"
            " and the 'VAT retained by the buyer' sale tax (-19%, SII code 15,"
            " with a tax account) in the Accounting settings first."
        ),
        "retention_partial": env._(
            "This SII VAT retention code is not supported on a received purchase"
            " invoice; supported product codes are %(codes)s besides the total"
            " retention (code 15).",
            codes=", ".join(sorted(PARTIAL_RETENTION_RATES, key=int)),
        ),
        "retention_unsupported": env._(
            "Each VAT retention of a received purchase invoice must state its"
            " official SII rate, appear once, and not exceed the VAT; a document"
            " with only total retentions must retain exactly its VAT."
        ),
        "retention_unmapped": env._(
            "Configure a sale-side retention tax for this SII retention code"
            " (negative official rate, same SII code, with a tax account) in"
            " the Accounting settings first."
        ),
        "customer_missing": env._(
            "Create the buyer as a customer with its valid Chilean RUT before"
            " preparing this invoice."
        ),
        "customer_ambiguous": env._(
            "More than one commercial customer matches the buyer RUT."
        ),
        "customer_archived": env._("The matching commercial customer is archived."),
        "duplicate_invoice": env._(
            "Another customer invoice or binding already reserves this buyer,"
            " document type and folio."
        ),
        "journal_name_collision": env._(
            "The DTE 46 sale journal already has an entry with this document"
            " number; use a dedicated journal for received purchase invoices."
        ),
        "link_unsupported": env._(
            "A received purchase invoice can only create a new customer invoice"
            " draft."
        ),
        "invoice_incompatible": env._(
            "The customer invoice differs from the signed purchase invoice."
        ),
        "tax_invalid": env._(
            "Taxable details use the configured sales VAT; exempt details have"
            " no tax."
        ),
        "account_invalid": env._(
            "Choose an active revenue account belonging to this company."
        ),
    }


def received_diagnostic(
    env, code, ordinal=0, source_value="", odoo_value="", severity="blocker"
):
    """A diagnostic for this bridge; inbox codes keep their shared wording."""
    messages = received_messages(env)
    if code not in messages:
        return diagnostic(
            env,
            code,
            ordinal=ordinal,
            source_value=source_value,
            odoo_value=odoo_value,
            severity=severity,
        )
    return dict(
        code=code,
        severity=severity,
        ordinal=ordinal,
        source_value=str(source_value),
        odoo_value=str(odoo_value),
        message=messages[code],
    )


def reject_received(env, code, **kwargs):
    raise IncomingAccountError(received_diagnostic(env, code, **kwargs))


def plain_tax(tax, company, use, rate, sii_code):
    """Whether a tax is an active, excluded, simple percent tax of the company."""
    return bool(
        tax
        and tax.company_id == company
        and tax.active
        and tax.type_tax_use == use
        and tax.amount_type == "percent"
        and decimal(tax.amount) == rate
        and tax.l10n_cl_sii_code == sii_code
        and not tax.price_include
        and not tax.include_base_amount
        and not tax.children_tax_ids
    )
