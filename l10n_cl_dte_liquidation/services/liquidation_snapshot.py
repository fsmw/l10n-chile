# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from decimal import ROUND_HALF_UP, Decimal

from odoo import fields
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_xml.services.invoice_snapshot import (
    InvoiceSnapshotService,
)

VAT_RATE = Decimal("19")
# SII Formato DTE, section F: a settlement invoice has 1 to 20 charge lines.
MAX_CHARGE_LINES = 20


class LiquidationSnapshotService(InvoiceSnapshotService):
    """Build the immutable fiscal input of a settlement invoice DTE 43.

    The commission agent (our company) settles to its principal the operations
    made on the principal's behalf. The Emisor is the company, the Receptor is
    the principal, and the posted vendor bill carries:

    * settled operations: positive lines with the 19% purchase VAT or no VAT,
      each with the SII type of the document that supported it (TpoDocLiq);
    * the agent's commissions and other charges: negative lines flagged as a
      charge, with the 19% sales VAT or no VAT (Comisiones block).

    The bill total is the amount payable to the principal, which is MntTotal.
    """

    SUPPORTED_DOCUMENT_CODES = frozenset({"43"})

    def validate_invoice_source(self, move, document_code):
        """Validate master data, lines and totals before a folio is reserved."""
        move.ensure_one()
        self._validate_invoice(move, str(document_code))
        self._settlement_body(move)

    def capture_invoice(self, move, caf_authorization, folio_number, document_code):
        """Return the serializable snapshot of the settlement invoice."""
        move.ensure_one()
        caf_authorization.ensure_one()
        document_code = str(document_code)
        company, activity_code = self._validate_invoice(move, document_code)
        self._validate_caf(caf_authorization, company, folio_number, document_code)
        lines, charges, totals = self._settlement_body(move)
        current_timestamp = self._chile_timestamp()
        return {
            "company_id": company.id,
            "document_code": document_code,
            "folio": int(folio_number),
            "certification": False,
            "issue_date": fields.Date.to_date(move.invoice_date).isoformat(),
            "document_timestamp": current_timestamp,
            "ted_timestamp": current_timestamp,
            "issuer": self._partner_snapshot(
                company.partner_id,
                activity=company.l10n_cl_activity_description,
                activity_code=activity_code,
            ),
            "receiver": self._partner_snapshot(move.partner_id.commercial_partner_id),
            "totals": totals,
            "lines": lines,
            "charges": charges,
        }

    # Lines -------------------------------------------------------------------

    def _settlement_body(self, move):
        """Return the Detalle lines, the Comisiones lines and the Totales."""
        operations, charge_lines = self._split_lines(move)
        lines, taxable_net, exempt_net = self._snapshot_lines(operations)
        for snapshot, line in zip(lines, operations, strict=True):
            snapshot["settled_document_code"] = line.dte_settled_document_code
        charges = [self._charge_snapshot(line) for line in charge_lines]
        totals = self._settlement_totals(move, taxable_net, exempt_net, charges)
        return lines, charges, totals

    def _split_lines(self, move):
        env = move.env
        lines = move.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        charges = lines.filtered("dte_liquidation_charge")
        operations = lines - charges
        if not charges or len(charges) > MAX_CHARGE_LINES:
            raise ValidationError(
                env._(
                    "A settlement invoice DTE 43 needs between 1 and 20 lines "
                    "flagged as a commission or other charge."
                )
            )
        if any(line.price_subtotal >= 0 for line in charges):
            raise ValidationError(
                env._(
                    "Commission and other charge lines reduce the amount owed to "
                    "the principal: enter them as negative lines."
                )
            )
        if any(line.price_subtotal <= 0 for line in operations):
            raise ValidationError(
                env._(
                    "Settled operations must be positive lines; negative "
                    "settled operations are not supported yet."
                )
            )
        if any(line.discount for line in operations):
            raise ValidationError(
                env._(
                    "A settlement invoice cannot carry line discounts; enter the "
                    "net unit price of the settled operation."
                )
            )
        if any(not line.dte_settled_document_code for line in operations):
            raise ValidationError(
                env._("Each settled operation needs its settled document type.")
            )
        return operations, charges

    def _charge_snapshot(self, line):
        """Return one Comisiones line: its net or exempt value and its VAT."""
        taxable = self._charge_taxable(line.tax_ids)
        value = self._integer_amount(-line.balance, "commission amount")
        vat = (value * VAT_RATE / 100).quantize(Decimal("1"), ROUND_HALF_UP)
        description = (line.name or line.product_id.name or "").strip()
        if not description:
            raise ValidationError(
                line.env._("Each commission or other charge needs a description.")
            )
        return {
            "movement": line.dte_liquidation_charge,
            "description": description[:60],
            "net": str(int(value)) if taxable else "0",
            "exempt": "0" if taxable else str(int(value)),
            "vat": str(int(vat)) if taxable else None,
        }

    def _line_clp_amounts(self, line):
        """Vendor bill lines are debits: their balance is the CLP subtotal."""
        subtotal = self._integer_amount(line.balance, "invoice line subtotal")
        return subtotal, self._decimal_text(line.price_unit)

    def _line_taxes(self, taxes):
        """Settled operations bear the 19% purchase VAT or are exempt."""
        return self._vat_taxable(taxes, "purchase"), None

    def _charge_taxable(self, taxes):
        """The agent's commission bears its own 19% sales VAT or is exempt."""
        return self._vat_taxable(taxes, "sale")

    def _vat_taxable(self, taxes, tax_use):
        charged = taxes.filtered("amount")
        if len(charged) > 1 or charged.filtered(
            lambda tax: tax.type_tax_use != tax_use
        ):
            raise ValidationError(
                taxes.env._(
                    "On a settlement invoice, settled operations carry one 19% "
                    "purchase VAT and commissions one 19% sales VAT, or no VAT."
                )
            )
        return self._is_taxable_19_percent(taxes)

    # Totals ------------------------------------------------------------------

    def _settlement_totals(self, move, taxable_net, exempt_net, charges):
        """Return Totales per field 124 of the SII DTE format.

        MntTotal = MntNeto + MntExe + IVA - ValComNeto - ValComIVA - ValComExe,
        the amount the agent pays the principal (the posted bill total).
        """
        env = move.env
        commission_net = sum(Decimal(charge["net"]) for charge in charges)
        commission_exempt = sum(Decimal(charge["exempt"]) for charge in charges)
        commission_vat = sum(Decimal(charge["vat"] or 0) for charge in charges)
        operation_vat, posted_commission_vat = self._posted_vat(move)
        if taxable_net and not operation_vat:
            raise ValidationError(
                env._("The taxable settled operations have no posted VAT amount.")
            )
        if posted_commission_vat != commission_vat:
            raise ValidationError(
                env._(
                    "The posted commission VAT differs from 19% of each "
                    "commission line; round taxes per line."
                )
            )
        untaxed = taxable_net + exempt_net - commission_net - commission_exempt
        total = untaxed + operation_vat - commission_vat
        if untaxed != abs(
            self._integer_amount(move.amount_untaxed_signed, "invoice untaxed amount")
        ) or total != abs(self._integer_amount(move.amount_total_signed, "total")):
            raise ValidationError(
                env._(
                    "The settlement invoice lines and taxes do not reconcile to "
                    "the posted bill amounts."
                )
            )
        return {
            "net": str(int(taxable_net)) if taxable_net else None,
            "exempt": str(int(exempt_net)) if exempt_net else None,
            "vat_rate": str(VAT_RATE) if taxable_net else None,
            "vat": str(int(operation_vat)) if taxable_net else None,
            "commission_net": str(int(commission_net)) if commission_net else None,
            "commission_exempt": (
                str(int(commission_exempt)) if commission_exempt else None
            ),
            "commission_vat": str(int(commission_vat)) if commission_vat else None,
            "total": str(int(total)),
        }

    def _posted_vat(self, move):
        """Return the posted operations VAT and commission VAT, both positive."""
        operation_vat = commission_vat = Decimal("0")
        for line in move.line_ids.filtered("tax_line_id"):
            if line.tax_line_id.type_tax_use == "sale":
                commission_vat -= self._decimal(line.balance)
            else:
                operation_vat += self._decimal(line.balance)
        return (
            self._integer_amount(operation_vat, "operations VAT amount"),
            self._integer_amount(commission_vat, "commission VAT amount"),
        )

    # Source and parties ------------------------------------------------------

    def _validate_invoice(self, move, document_code):
        env = move.env
        if document_code not in self.SUPPORTED_DOCUMENT_CODES:
            raise ValidationError(
                env._(
                    "DTE type %(code)s is not supported by this issuance flow.",
                    code=document_code,
                )
            )
        if move.state != "posted" or move.move_type != "in_invoice":
            raise ValidationError(
                env._(
                    "A settlement invoice DTE 43 is issued from a posted vendor "
                    "bill of the principal."
                )
            )
        if move.l10n_latam_document_type_id.code != document_code:
            raise ValidationError(
                env._("The selected DTE type does not match the accounting document.")
            )
        company = move.company_id
        if company.partner_id.country_id.code != "CL":
            raise ValidationError(
                env._("The issuing company must be registered in Chile.")
            )
        if move.currency_id != company.currency_id:
            raise ValidationError(
                env._("A settlement invoice DTE 43 must be in the company currency.")
            )
        if not move.invoice_date:
            raise ValidationError(env._("The invoice must have an issue date."))
        errors = self._fiscal_master_data_errors(move, document_code)
        if errors:
            raise ValidationError(
                env._(
                    "Complete the required fiscal data before issuing the DTE:\n%s",
                    "\n".join(f"• {error}" for error in errors),
                )
            )
        return company, company.dte_sii_activity_code.strip()

    def fiscal_master_data_errors(self, company, partner, document_code):
        """The principal is the Receptor and must be a Chilean taxpayer."""
        env = company.env
        caf_model = env["l10n_cl.caf.authorization"]
        errors = self.issuer_master_data_errors(company)
        principal = partner.commercial_partner_id
        principal_vat = caf_model._normalize_vat(principal.vat)
        if not caf_model._is_valid_vat(principal_vat):
            errors.append(env._("Principal: enter a valid Chilean RUT."))
        elif principal_vat == caf_model._normalize_vat(company.partner_id.vat):
            errors.append(
                env._("Principal: the principal cannot be the issuing company.")
            )
        if not (principal.name or "").strip():
            errors.append(env._("Principal: enter a name for the DTE recipient."))
        if not (principal.l10n_cl_activity_description or "").strip():
            errors.append(env._("Principal: enter the economic activity (giro)."))
        if not " ".join(
            part.strip() for part in (principal.street, principal.street2) if part
        ):
            errors.append(env._("Principal: enter the address."))
        if not (principal.city or "").strip():
            errors.append(env._("Principal: enter the commune."))
        return errors
