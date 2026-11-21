# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.addons.l10n_cl_dte_inbox_account.services.common import (
    IncomingAccountError,
    decimal,
    diagnostic,
)

# A purchase invoice the buyer issued to us documents our own sale.
PURCHASE_CODE = "46"
SALE_MOVE_TYPE = "out_invoice"

# SII ImptoReten code for VAT retained in full by the buyer, and its rate.
TOTAL_RETENTION_CODE = "15"
TOTAL_RETENTION_RATE = 19
VAT_RATE = 19
VAT_SII_CODE = 14

# Partial VAT retention codes (cattle, wood, scrap, ...); out of this slice.
PARTIAL_RETENTION_CODES = frozenset(str(code) for code in range(30, 49))


def received_messages(env):
    return {
        "received46_not_configured": env._(
            "Configure the DTE 46 sale journal, the sales VAT 19% (SII code 14)"
            " and the 'VAT retained by the buyer' sale tax (-19%, SII code 15,"
            " with a tax account) in the Accounting settings first."
        ),
        "retention_partial": env._(
            "Partial VAT retention codes (30 to 48) are not supported yet; only"
            " total retention (code 15) at 19% can be booked."
        ),
        "retention_unsupported": env._(
            "A received purchase invoice must state the total VAT retention"
            " (code 15) at 19% for exactly its VAT amount."
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
