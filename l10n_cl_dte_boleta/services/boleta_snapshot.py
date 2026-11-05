# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from decimal import Decimal, InvalidOperation

from odoo import fields
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_xml.services.invoice_snapshot import InvoiceSnapshotService


class BoletaSnapshotService:
    """Validate and snapshot posted invoices for Chilean boleta DTEs."""

    SUPPORTED_DOCUMENT_CODES = frozenset({"39", "41"})
    GENERIC_RECEIVER_RUT = "66666666-6"
    SERVICE_INDICATOR = "3"
    MAX_DETAIL_LINES = 1000

    def __init__(self, env=None):
        self.env = env

    def validate_invoice_source(self, move, document_code):
        """Validate fiscal master data and supported source conditions."""
        self.env = move.env
        move.ensure_one()
        document_code = str(document_code)
        if document_code not in self.SUPPORTED_DOCUMENT_CODES:
            raise ValidationError(
                move.env._(
                    "Only Chilean boletas 39 and 41 are supported by this adapter."
                )
            )
        if move.state != "posted" or move.move_type != "out_invoice":
            raise ValidationError(
                move.env._(
                    "Only posted customer invoices can be issued as electronic boletas."
                )
            )
        if move.company_id.country_id.code != "CL":
            raise ValidationError(
                move.env._("The company must be registered in Chile.")
            )
        if move.currency_id.name != "CLP":
            raise ValidationError(
                move.env._("Electronic boletas must be issued in CLP.")
            )
        if not move.invoice_date:
            raise ValidationError(
                move.env._("Set the invoice date before issuing a boleta.")
            )
        if move.company_id.dte_sii_environment != "demo":
            raise ValidationError(
                move.env._("Boleta issuance is currently available only in Demo mode.")
            )

        issuer = move.company_id.partner_id
        missing = []
        if not self._is_valid_chilean_vat(move.env, issuer.vat):
            missing.append(move.env._("valid company RUT"))
        if not (issuer.name or "").strip():
            missing.append(move.env._("company legal name"))
        if not (move.company_id.l10n_cl_activity_description or "").strip():
            missing.append(move.env._("company economic activity"))
        activity_code = (move.company_id.dte_sii_activity_code or "").strip()
        if not activity_code.isdigit() or int(activity_code or "0") <= 0:
            missing.append(move.env._("valid SII activity code"))
        if not (issuer.street or "").strip() or not (issuer.city or "").strip():
            missing.append(move.env._("company address and commune"))
        if missing:
            raise ValidationError(
                move.env._("Complete the following issuer data before issuing: %s")
                % ", ".join(missing)
            )

        receiver = move.partner_id.commercial_partner_id
        if not (receiver.name or "").strip():
            raise ValidationError(
                move.env._(
                    "Set a receiver name; it is required for sales and services."
                )
            )
        if not (receiver.vat or "").strip():
            raise ValidationError(
                move.env._("Set a receiver RUT or use the generic final-consumer RUT.")
            )
        if not self._is_generic_receiver_rut(receiver.vat) and not (
            self._is_valid_chilean_vat(move.env, receiver.vat)
        ):
            raise ValidationError(move.env._("Enter a valid Chilean receiver RUT."))
        self._validate_lines(move, document_code)
        self._validate_annual_receiver_threshold(move, receiver)
        return True

    def capture_invoice(self, move, caf_authorization, folio_number, document_code):
        """Return deterministic, JSON-safe input for TED and XML composition."""
        self.validate_invoice_source(move, document_code)
        if caf_authorization.document_code != str(document_code):
            raise ValidationError(
                move.env._("The CAF document type must match the boleta.")
            )
        if caf_authorization.company_id != move.company_id:
            raise ValidationError(move.env._("The CAF belongs to another company."))
        caf_authorization._check_caf_integrity()
        invoice_service = InvoiceSnapshotService()
        lines, taxable_net, exempt_net = self._snapshot_lines(move)
        total = self._integer(move.amount_total, move.env._("total"))
        tax = self._integer(move.amount_tax, move.env._("VAT"))
        net = self._integer(move.amount_untaxed, move.env._("net amount"))
        if taxable_net + exempt_net != net or net + tax != total:
            raise ValidationError(
                move.env._(
                    "The posted invoice totals do not reconcile with its detail lines."
                )
            )
        if str(document_code) == "41" and (taxable_net or tax):
            raise ValidationError(
                move.env._("Boleta type 41 can contain only exempt lines.")
            )
        activity_code = (move.company_id.dte_sii_activity_code or "").strip()
        issuer = invoice_service._partner_snapshot(
            move.company_id.partner_id,
            activity=move.company_id.l10n_cl_activity_description,
            activity_code=activity_code,
        )
        receiver = invoice_service._partner_snapshot(
            move.partner_id.commercial_partner_id
        )
        if self._is_generic_receiver_rut(receiver["vat"]):
            receiver["vat"] = self.GENERIC_RECEIVER_RUT
        else:
            receiver["vat"] = self._xml_rut(receiver["vat"])
        issuer["vat"] = self._xml_rut(issuer["vat"])
        receiver_partner = move.partner_id.commercial_partner_id
        receiver["email"] = (receiver_partner.email or "").strip()
        receiver["phone"] = (
            receiver_partner.phone or receiver_partner.mobile or ""
        ).strip()
        stamp = invoice_service._chile_timestamp()
        over_threshold = self._integer(move.amount_total, move.env._("total")) > (
            self._annual_135_uf_limit(move)
        )
        return {
            "company_id": move.company_id.id,
            "document_code": str(document_code),
            "folio": int(folio_number),
            "issue_date": fields.Date.to_date(move.invoice_date).isoformat(),
            "document_timestamp": stamp,
            "ted_timestamp": stamp,
            "service_indicator": self.SERVICE_INDICATOR,
            "net_amount_indicator": "2" if str(document_code) == "39" else None,
            "payment_method": (
                move.dte_boleta_payment_method if over_threshold else None
            ),
            "issuer": issuer,
            "receiver": receiver,
            "totals": {
                "net": str(int(net)) if taxable_net else None,
                "exempt": str(int(exempt_net)) if exempt_net else None,
                "vat_rate": "19" if taxable_net else None,
                "vat": str(int(tax)) if taxable_net else None,
                "total": str(int(total)),
            },
            "lines": lines,
        }

    def _validate_lines(self, move, document_code):
        lines, taxable_net, _exempt_net = self._snapshot_lines(move)
        if str(document_code) == "41" and taxable_net:
            raise ValidationError(
                move.env._("Boleta type 41 requires all lines to be exempt.")
            )
        if getattr(move, "invoice_cash_rounding_id", False):
            raise ValidationError(
                move.env._("Cash rounding is not supported for boletas yet.")
            )
        if move.line_ids.filtered(lambda line: line.display_type == "payment_term"):
            if move.amount_total != move.amount_untaxed + move.amount_tax:
                raise ValidationError(
                    move.env._("Invoice payment balances are not supported.")
                )
        return lines

    def _validate_annual_receiver_threshold(self, move, receiver):
        """Fail closed when the annual CLP limit cannot be proven locally."""
        limit = self._annual_135_uf_limit(move)
        if self._integer(move.amount_total, move.env._("total")) > limit:
            if self._is_generic_receiver_rut(receiver.vat):
                raise ValidationError(
                    move.env._(
                        "This boleta exceeds the annual 135 UF limit. Identify the "
                        "person who paid and issue a new boleta with their RUT."
                    )
                )
            if not move.dte_boleta_payment_method:
                raise ValidationError(
                    move.env._(
                        "Choose the payment method for a boleta over the annual "
                        "135 UF limit."
                    )
                )
            if move.dte_boleta_payment_method == "1":
                payer = move.partner_id.commercial_partner_id
                if (
                    not (payer.email or "").strip()
                    or not (payer.phone or payer.mobile or "").strip()
                ):
                    raise ValidationError(
                        move.env._(
                            "Cash boletas over 135 UF require the receiver email "
                            "and phone."
                        )
                    )

    def _annual_135_uf_limit(self, move):
        return self._annual_135_uf_limit_for(
            move.env, move.company_id, move.invoice_date
        )

    def _annual_135_uf_limit_for(self, env, company, source_date):
        self.env = env
        year = fields.Date.to_date(source_date).year
        cutoff = fields.Date.to_date(f"{year - 1}-12-31")
        uf = env.ref("l10n_cl.UF", raise_if_not_found=False)
        clp = env.ref("base.CLP", raise_if_not_found=False)
        if not uf or not clp:
            raise ValidationError(env._("The UF and CLP currencies are required."))
        available_rates = uf.rate_ids.filtered(
            lambda rate: (
                rate.name <= cutoff
                and (not rate.company_id or rate.company_id == company)
            )
        )
        if not available_rates:
            raise ValidationError(
                env._("The annual 135 UF boleta threshold is unavailable for %s.")
                % cutoff
            )
        try:
            limit = uf._convert(135.0, clp, company, cutoff)
        except (ValueError, ZeroDivisionError) as error:
            raise ValidationError(
                env._("The annual 135 UF boleta threshold is unavailable for %s.")
                % cutoff
            ) from error
        limit = Decimal(str(limit or 0))
        if limit <= 0:
            raise ValidationError(
                env._("The annual 135 UF boleta threshold is unavailable for %s.")
                % cutoff
            )
        return limit

    def _is_generic_receiver_rut(self, vat):
        normalized = (vat or "").replace(".", "").replace("-", "").replace(" ", "")
        return normalized.upper() == "666666666"

    def _is_valid_chilean_vat(self, env, vat):
        if not (vat or "").strip():
            return False
        caf_model = env["l10n_cl.caf.authorization"]
        return caf_model._is_valid_vat(caf_model._normalize_vat(vat))

    def _xml_rut(self, vat):
        return (vat or "").replace(".", "").replace(" ", "").strip()

    def _snapshot_lines(self, move):
        self.env = move.env
        invoice_lines = move.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        if not invoice_lines:
            raise ValidationError(
                move.env._("The invoice needs at least one product line.")
            )
        if len(invoice_lines) > self.MAX_DETAIL_LINES:
            raise ValidationError(
                move.env._("A boleta supports at most 1000 detail lines.")
            )
        composer = InvoiceSnapshotService()
        snapshots = []
        taxable_net = Decimal("0")
        exempt_net = Decimal("0")
        for line in invoice_lines:
            taxable = composer._is_taxable_19_percent(line.tax_ids)
            subtotal = self._integer(line.price_subtotal, move.env._("line subtotal"))
            if subtotal < 0:
                raise ValidationError(
                    move.env._("Negative invoice lines are not supported.")
                )
            taxable_net += subtotal if taxable else Decimal("0")
            exempt_net += Decimal("0") if taxable else subtotal
            name = (line.product_id.name or line.name or "").strip()
            description = (line.name or name).strip()
            if not name or len(name) > 80 or len(description) > 1000:
                raise ValidationError(
                    move.env._(
                        "Boleta product names must be 1-80 and descriptions at "
                        "most 1000 characters."
                    )
                )
            quantity = self._decimal(line.quantity, move.env._("quantity"))
            if quantity <= 0:
                raise ValidationError(
                    move.env._("Boleta quantities must be greater than zero.")
                )
            discount = self._decimal(line.discount, move.env._("discount"))
            factor = Decimal("1") - discount / Decimal("100")
            unit_price = subtotal / quantity / factor if factor else Decimal("0")
            snapshots.append(
                {
                    "name": name,
                    "description": description,
                    "quantity": self._decimal_text(quantity),
                    "uom": (line.product_uom_id.name or "")[:4],
                    "unit_price": self._decimal_text(unit_price),
                    "discount_percent": (
                        self._decimal_text(discount) if discount else None
                    ),
                    "subtotal": str(int(subtotal)),
                    "exempt": not taxable,
                }
            )
        return snapshots, taxable_net, exempt_net

    def _integer(self, value, label):
        amount = self._decimal(value, label)
        if amount != amount.to_integral_value():
            raise ValidationError(
                self.env._("CLP %s must be an integer amount.") % label
            )
        return amount

    def _decimal(self, value, label):
        try:
            return Decimal(str(value or 0))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise ValidationError(self.env._("Invalid boleta %s.") % label) from error

    def _decimal_text(self, value):
        return format(value.normalize(), "f")
