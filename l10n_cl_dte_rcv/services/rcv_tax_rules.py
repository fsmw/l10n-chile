# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Compare the amounts and taxes of an RCV document with a journal entry."""

from .rcv_fiscal_evidence import PURCHASE_VAT_COMPONENTS, rcv_amounts

ZERO_VAT_DOCUMENT_CODES = ("33", "61")
EXEMPT_SALE_CODES = ("34", "110")
CREDIT_NOTE_CODE = "61"
EXEMPT_INVOICE_CODE = "34"
CLP = "CLP"


def move_period(move):
    """Return the accounting period of ``move`` as ``YYYYMM``."""
    return move.date.strftime("%Y%m") if move.date else False


def _has_zero_vat_amounts(document):
    currency = document.currency_id
    return (
        currency.is_zero(document.net_amount)
        and currency.is_zero(document.vat_amount)
        and currency.is_zero(document.vat_not_recoverable)
        and currency.is_zero(document.common_vat)
        and currency.is_zero(document.exempt_amount - document.total_amount)
    )


class RcvTaxRules:
    """Fiscal comparison rules between RCV amounts and booked amounts."""

    def __init__(self, env, evidence):
        self.env = env
        self.evidence = evidence

    # Booked bases

    def booked_bases(self, document, move):
        """Return ``(net, exempt, zero_tax_exempt)`` booked on ``move``."""
        currency = document.currency_id
        exempt = self.evidence.move_exempt_amount(move)
        zero_tax_exempt = self.proven_zero_tax_exempt(document, move, exempt)
        untaxed = abs(move.amount_untaxed_signed)
        if zero_tax_exempt and document.document_type_code == CREDIT_NOTE_CODE:
            # An exempt credit note may book no exempt tax at all: its posted
            # untaxed balance is the exempt base once the source proves it.
            exempt = untaxed
        elif (
            not zero_tax_exempt
            and currency.is_zero(exempt)
            and not currency.is_zero(document.exempt_amount)
            and currency.is_zero(untaxed - document.net_amount - document.exempt_amount)
        ):
            # Exempt lines may have no tax at all: the untaxed total then
            # still equals the RCV net plus exempt amounts.
            exempt = document.exempt_amount
        return untaxed - exempt, exempt, zero_tax_exempt

    # Zero VAT exempt documents

    def proven_zero_tax_exempt(self, document, move, exempt_amount):
        """Recognize only fully exempt sales whose posted balances prove zero VAT."""
        if document.document_type_code == CREDIT_NOTE_CODE:
            return self._proven_exempt_credit_note(document, move)
        currency = document.currency_id
        if (
            document.operation != "sale"
            or document.document_type_code not in EXEMPT_SALE_CODES
            or move.state != "posted"
            or not _has_zero_vat_amounts(document)
            or not currency.is_zero(exempt_amount - document.total_amount)
            or move_period(move) != document.tax_period
        ):
            return False
        return not move.line_ids.filtered(
            lambda line: line.tax_line_id
            and line.tax_line_id.amount_type == "percent"
            and line.tax_line_id.amount
        )

    def _proven_exempt_credit_note(self, document, move):
        """Prove an exempt credit note against its posted exempt invoice."""
        source = move.reversed_entry_id
        currency = document.currency_id
        if not (
            document.operation == "sale"
            and move.state == "posted"
            and move.move_type == "out_refund"
            and move.l10n_latam_document_type_id.code == CREDIT_NOTE_CODE
            and source.state == "posted"
            and source.move_type == "out_invoice"
            and source.l10n_latam_document_type_id.code == EXEMPT_INVOICE_CODE
            and source.company_id == move.company_id == document.company_id
            and move.partner_id == source.partner_id
            and currency.name == CLP
            and move.currency_id == source.currency_id == currency
            and _has_zero_vat_amounts(document)
            and move_period(move) == document.tax_period
        ):
            return False
        return (
            currency.is_zero(abs(move.amount_untaxed_signed) - document.total_amount)
            and currency.is_zero(abs(move.amount_total_signed) - document.total_amount)
            and currency.is_zero(
                abs(source.amount_untaxed_signed) - abs(source.amount_total_signed)
            )
            and currency.is_zero(
                self.evidence.move_exempt_amount(source)
                - abs(source.amount_untaxed_signed)
            )
            and not self.evidence.move_has_nonzero_taxes(move)
            and not self.evidence.move_has_nonzero_taxes(source)
        )

    # VAT

    def compare_vat(self, document, move):
        """Return ``(tax_comparison_state, different)`` for the fiscal VAT."""
        if not document.tax_breakdown_available:
            return "not_comparable", False
        if document.operation == "purchase":
            rcv_vat = rcv_amounts(document)["fiscal_vat"]
        else:
            rcv_vat = document.vat_amount
        booked_vat = self.evidence.odoo_fiscal_vat(move)
        if booked_vat is None:
            return "not_comparable", False
        different = not document.currency_id.is_zero(rcv_vat - booked_vat)
        return ("different" if different else "matched"), different

    def zero_vat_conflict(self, document):
        """Tell whether a purchase declares zero VAT but leaves a residual.

        Such a row cannot be reconciled from the RCV alone: the residual is
        not classified as VAT without independent source evidence.
        """
        values = rcv_amounts(document)
        currency = document.currency_id
        return bool(
            document.operation == "purchase"
            and document.document_type_code in ZERO_VAT_DOCUMENT_CODES
            and values["complete"]
            and currency.is_zero(values["fiscal_vat"])
            and not currency.is_zero(values["unexplained_residual"])
        )

    def compare_allocation(self, document, move):
        """Compare the purchase VAT allocation; return ``(state, note)``."""
        if document.operation != "purchase" or not document.tax_breakdown_available:
            return "not_comparable", False
        booked = self.evidence.tax_component_amounts(move)
        if booked is None:
            return "not_comparable", self.env._(
                "The booked VAT could not be classified as recoverable, "
                "non-recoverable or common use VAT."
            )
        differences = [
            self.env._(
                "%(component)s: RCV %(rcv)s, booked %(booked)s",
                component=document._fields[field_name]._description_string(self.env),
                rcv=document[field_name],
                booked=booked[field_name],
            )
            for field_name, _tag, _expression in PURCHASE_VAT_COMPONENTS
            if not document.currency_id.is_zero(
                document[field_name] - booked[field_name]
            )
        ]
        if not differences:
            return "matched", False
        return "different", self.env._(
            "Only the VAT allocation differs: %(details)s.",
            details="; ".join(differences),
        )

    # Generic exports

    def export_bases(self, move):
        """Return ``(net, exempt)`` of a posted export, or None if unclassifiable."""
        net = exempt = 0.0
        for line in move.invoice_line_ids.filtered(
            lambda item: item.display_type == "product"
        ):
            taxes = line.tax_ids.flatten_taxes_hierarchy()
            if taxes.filtered(lambda tax: tax.amount_type not in ("percent", "fixed")):
                return None
            if all(not tax.amount for tax in taxes):
                exempt -= line.balance
            else:
                net -= line.balance
        return net, exempt

    def compare_export_vat(self, document, move):
        """Require an RCV breakdown and only identified VAT lines on the export."""
        if not document.tax_breakdown_available:
            return "not_comparable", False
        tax_lines = move.line_ids.filtered("tax_line_id")
        if tax_lines - self.evidence.vat_tax_lines(move):
            return "not_comparable", False
        booked_vat = abs(sum(tax_lines.mapped("balance")))
        different = not document.currency_id.is_zero(document.vat_amount - booked_vat)
        return ("different" if different else "matched"), different
