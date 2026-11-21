# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from decimal import Decimal

from odoo import fields
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_xml.services.invoice_snapshot import (
    InvoiceSnapshotService,
)

# SII code of the total VAT retention (Retención Total IVA) and its only rate.
TOTAL_RETENTION_CODE = 15
TOTAL_RETENTION_RATE = -19
# SII codes of partial VAT retentions (Retención parcial: legumes, cattle, ...).
PARTIAL_RETENTION_CODES = frozenset(range(30, 49))


class PurchaseInvoiceSnapshotService(InvoiceSnapshotService):
    """Build the immutable fiscal input of a purchase invoice DTE 46.

    The buyer (our company) issues the document on behalf of a seller that
    does not invoice and retains the whole VAT. The Emisor is the company,
    the Receptor is the supplier, and the posted vendor bill carries 19% VAT
    plus the total VAT retention (SII code 15) on each taxable line.
    """

    SUPPORTED_DOCUMENT_CODES = frozenset({"46"})

    def validate_invoice_source(self, move, document_code):
        """Validate master data and line taxes before a folio is reserved."""
        move.ensure_one()
        self._validate_invoice(move, str(document_code))
        for line in self._purchase_lines(move):
            self._line_taxes(line.tax_ids)

    def capture_invoice(self, move, caf_authorization, folio_number, document_code):
        """Return the serializable snapshot of the purchase invoice."""
        move.ensure_one()
        caf_authorization.ensure_one()
        document_code = str(document_code)
        company, activity_code = self._validate_invoice(move, document_code)
        self._validate_caf(caf_authorization, company, folio_number, document_code)
        line_snapshots, taxable_net, exempt_net = self._snapshot_lines(
            self._purchase_lines(move)
        )
        totals = self._purchase_totals(move, taxable_net, exempt_net)
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
            "lines": line_snapshots,
            "global_discounts": [],
            "other_currency": None,
        }

    def _purchase_lines(self, move):
        lines = move.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        if any(line.price_subtotal < 0 for line in lines):
            raise ValidationError(
                move.env._("A purchase invoice DTE 46 cannot contain negative lines.")
            )
        return lines

    def _purchase_totals(self, move, taxable_net, exempt_net):
        """Return Totales: MntTotal is what the buyer pays the seller."""
        vat_total, retained = self._posted_tax_amounts(move)
        if taxable_net and (not vat_total or retained != vat_total):
            raise ValidationError(
                move.env._(
                    "The total VAT retention of a purchase invoice must equal "
                    "its posted 19% VAT."
                )
            )
        total = taxable_net + exempt_net + vat_total - retained
        untaxed = abs(
            self._integer_amount(move.amount_untaxed_signed, "invoice untaxed amount")
        )
        if taxable_net + exempt_net != untaxed:
            raise ValidationError(
                move.env._(
                    "Invoice lines do not reconcile to the posted untaxed amount."
                )
            )
        if total != abs(self._integer_amount(move.amount_total_signed, "total")):
            raise ValidationError(
                move.env._(
                    "The purchase invoice total does not reconcile to its taxes."
                )
            )
        retentions = []
        if retained:
            retentions.append(
                {
                    "code": str(TOTAL_RETENTION_CODE),
                    "rate": str(abs(TOTAL_RETENTION_RATE)),
                    "amount": str(int(retained)),
                }
            )
        return {
            "net": str(int(taxable_net)) if taxable_net else None,
            "exempt": str(int(exempt_net)) if exempt_net else None,
            "vat_rate": "19" if taxable_net else None,
            "vat": str(int(vat_total)) if taxable_net else None,
            "additional_taxes": retentions,
            "total": str(int(total)),
        }

    def _posted_tax_amounts(self, move):
        """Return the posted VAT and the retained VAT, both positive."""
        vat_total = retained = Decimal("0")
        for line in move.line_ids.filtered("tax_line_id"):
            balance = self._decimal(line.balance)
            if line.tax_line_id.amount < 0:
                retained -= balance
            else:
                vat_total += balance
        return (
            self._integer_amount(vat_total, "invoice tax amount"),
            self._integer_amount(retained, "retained VAT amount"),
        )

    def _validate_invoice(self, move, document_code):
        if document_code not in self.SUPPORTED_DOCUMENT_CODES:
            raise ValidationError(
                move.env._(
                    "DTE type %(code)s is not supported by this issuance flow.",
                    code=document_code,
                )
            )
        if move.state != "posted" or move.move_type != "in_invoice":
            raise ValidationError(
                move.env._(
                    "A purchase invoice DTE 46 is issued from a posted vendor bill."
                )
            )
        if move.l10n_latam_document_type_id.code != document_code:
            raise ValidationError(
                move.env._(
                    "The selected DTE type does not match the accounting document."
                )
            )
        company = move.company_id
        if company.partner_id.country_id.code != "CL":
            raise ValidationError(
                move.env._("The issuing company must be registered in Chile.")
            )
        if move.currency_id != company.currency_id:
            raise ValidationError(
                move.env._("A purchase invoice DTE 46 must be in the company currency.")
            )
        if not move.invoice_date:
            raise ValidationError(move.env._("The invoice must have an issue date."))
        errors = self._fiscal_master_data_errors(move, document_code)
        if errors:
            raise ValidationError(
                move.env._(
                    "Complete the required fiscal data before issuing the DTE:\n%s",
                    "\n".join(f"• {error}" for error in errors),
                )
            )
        return company, company.dte_sii_activity_code.strip()

    def fiscal_master_data_errors(self, company, partner, document_code):
        """The supplier is the Receptor and must be a Chilean taxpayer."""
        caf_model = company.env["l10n_cl.caf.authorization"]
        errors = self.issuer_master_data_errors(company)
        supplier = partner.commercial_partner_id
        supplier_vat = caf_model._normalize_vat(supplier.vat)
        if not caf_model._is_valid_vat(supplier_vat):
            errors.append(company.env._("Supplier: enter a valid Chilean RUT."))
        elif supplier_vat == caf_model._normalize_vat(company.partner_id.vat):
            errors.append(
                company.env._("Supplier: the seller cannot be the issuing company.")
            )
        if not (supplier.name or "").strip():
            errors.append(
                company.env._("Supplier: enter a name for the DTE recipient.")
            )
        if not " ".join(
            part.strip() for part in (supplier.street, supplier.street2) if part
        ):
            errors.append(company.env._("Supplier: enter the seller address."))
        if not (supplier.city or "").strip():
            errors.append(company.env._("Supplier: enter the seller commune."))
        return errors

    def _line_clp_amounts(self, line):
        """Vendor bill lines are debits: their balance is the CLP subtotal."""
        subtotal = self._integer_amount(line.balance, "invoice line subtotal")
        return subtotal, self._decimal_text(line.price_unit)

    def _line_taxes(self, taxes):
        """Return whether the line bears VAT and its retention code ("15")."""
        retention = self._line_retention(taxes)
        if taxes.filtered(
            lambda tax: tax.l10n_cl_sii_code in self.ADDITIONAL_TAX_CODES
        ):
            raise ValidationError(
                taxes.env._(
                    "Additional SII taxes are not supported on a purchase invoice."
                )
            )
        taxable = self._is_taxable_19_percent(taxes - retention)
        if retention and not taxable:
            raise ValidationError(
                taxes.env._(
                    "The total VAT retention applies only to lines with 19% VAT."
                )
            )
        if taxable and not retention:
            raise ValidationError(
                taxes.env._(
                    "Each taxable line of a purchase invoice must carry the total "
                    "VAT retention (SII code 15 at 19%)."
                )
            )
        return taxable, str(TOTAL_RETENTION_CODE) if retention else None

    def _line_retention(self, taxes):
        """Return the total VAT retention and refuse every other withholding."""
        withholdings = taxes.filtered(
            lambda tax: tax.amount < 0
            or tax.l10n_cl_sii_code == TOTAL_RETENTION_CODE
            or tax.l10n_cl_sii_code in PARTIAL_RETENTION_CODES
        )
        if withholdings.filtered(
            lambda tax: tax.l10n_cl_sii_code in PARTIAL_RETENTION_CODES
        ):
            raise ValidationError(
                taxes.env._(
                    "Partial VAT retentions (SII codes 30 to 48) are not supported "
                    "on a purchase invoice yet."
                )
            )
        if withholdings.filtered(
            lambda tax: tax.l10n_cl_sii_code != TOTAL_RETENTION_CODE
            or tax.amount_type != "percent"
            or tax.amount != TOTAL_RETENTION_RATE
        ):
            raise ValidationError(
                taxes.env._(
                    "A purchase invoice supports only the total VAT retention "
                    "(SII code 15 at 19%); remove other withholdings such as "
                    "second category taxes."
                )
            )
        if len(withholdings) > 1:
            raise ValidationError(
                taxes.env._("An invoice line supports one total VAT retention.")
            )
        return withholdings
