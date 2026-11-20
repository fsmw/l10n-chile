# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tools.translate import LazyTranslate

from odoo.addons.l10n_cl_dte_xml.services.invoice_snapshot import InvoiceSnapshotService

from .export_reference_service import TEXT, ExportReferenceService

_lt = LazyTranslate(__name__)

# SII rule for export documents: the receiver has no Chilean RUT.
FOREIGN_RECEIVER_RUT = "55555555-5"
CLP_SII_NAME = "PESO CL"
MAX_DETAIL_LINES = 60
MAX_RATE = Decimal("999999.9999")
QUANTITY_DIGITS = Decimal("0.000001")
RATE_DIGITS = Decimal("0.0001")
# Customs data the export blueprint requires before a 110 is issued.
GOODS_REQUIRED = (
    ("dte_export_payment_id", "export payment method"),
    ("dte_export_sale_mode_id", "sale mode"),
    ("dte_export_sale_clause_id", "sale clause"),
    ("dte_export_clause_total", "sale clause total"),
    ("dte_export_transport_id", "transport mode"),
    ("dte_export_destination_id", "destination country"),
)
SERVICE_REQUIRED = {
    "3": (("dte_export_transport_id", "transport mode"),),
    "4": (),
    "5": (("dte_export_transport_id", "transport mode"),),
}
SERVICE_REFERENCE = {"3": "812", "5": "811"}
WEIGHTS = (
    ("dte_export_tare", "dte_export_tare_unit_id"),
    ("dte_export_gross_weight", "dte_export_gross_unit_id"),
    ("dte_export_net_weight", "dte_export_net_unit_id"),
)


class ExportSnapshotService:
    """Validate and capture an export invoice for the SII Exportaciones DTE."""

    MOVE_TYPES = {"110": "out_invoice", "111": "out_invoice", "112": "out_refund"}
    SUPPORTED_DOCUMENT_CODES = frozenset(MOVE_TYPES)

    def validate_source(self, move, document_code):
        """Reject an export source that cannot become a valid export DTE."""
        move.ensure_one()
        env = move.env
        code = str(document_code)
        if code not in self.SUPPORTED_DOCUMENT_CODES:
            raise ValidationError(env._("The export document type is not supported."))
        if move.state != "posted" or move.move_type != self.MOVE_TYPES[code]:
            raise ValidationError(
                env._("Only the posted customer document can be issued as an export.")
            )
        if move.dte_document_type_code != str(document_code):
            raise ValidationError(
                env._("The invoice document type must be the export type.")
            )
        if move.company_id.country_id.code != "CL":
            raise ValidationError(env._("The issuing company must be Chilean."))
        errors = self._issuer_errors(move.company_id)
        if code == "110":
            errors += self._receiver_errors(move)
            errors += self._customs_errors(move)
        errors += self._currency_errors(move)
        if errors:
            raise ValidationError("\n".join(errors))
        if code != "110":
            ExportReferenceService(env).validate(move, code)
        self._lines(move)
        return True

    def capture(self, move, caf_authorization, folio_number, document_code):
        """Return deterministic, JSON-safe input for the TED and the XML."""
        self.validate_source(move, document_code)
        if caf_authorization.document_code != str(document_code):
            raise ValidationError(move.env._("The CAF type must match the export."))
        if caf_authorization.company_id != move.company_id:
            raise ValidationError(move.env._("The CAF belongs to another company."))
        caf_authorization._check_caf_integrity()
        if str(document_code) != "110":
            return self._capture_note(move, folio_number, str(document_code))
        stamp = InvoiceSnapshotService()._chile_timestamp()
        lines = self._lines(move)
        totals = self._totals(move, lines)
        return {
            "schema_version": 1,
            "company_id": move.company_id.id,
            "source_model": "account.move",
            "source_id": move.id,
            "document_code": str(document_code),
            "folio": int(folio_number),
            "issue_date": fields.Date.to_date(move.invoice_date).isoformat(),
            "document_timestamp": stamp,
            "ted_timestamp": stamp,
            "issuer": self._issuer(move.company_id),
            "receiver": self._receiver(move),
            "identification": self._identification(move),
            "currency": self._currency(move),
            "totals": totals,
            "other_currency": self._other_currency(move, totals),
            "customs": self._customs(move, lines),
            "lines": lines,
            "references": self._references(move),
        }

    def _capture_note(self, move, folio_number, document_code):
        """A note keeps the recipient and customs profile of its root 110."""
        references = ExportReferenceService(move.env)
        root = references.root(move)
        references.lock_root(root)
        root, root_snapshot = references.validate(move, document_code)
        stamp = InvoiceSnapshotService()._chile_timestamp()
        lines = self._lines(move)
        text_only = move.dte_export_correction == TEXT
        totals = self._totals(move, lines, allow_zero=text_only)
        customs = dict(root_snapshot["customs"], total_items=len(lines))
        return {
            "schema_version": 1,
            "company_id": move.company_id.id,
            "source_model": "account.move",
            "source_id": move.id,
            "document_code": document_code,
            "folio": int(folio_number),
            "issue_date": fields.Date.to_date(move.invoice_date).isoformat(),
            "document_timestamp": stamp,
            "ted_timestamp": stamp,
            "issuer": self._issuer(move.company_id),
            "receiver": root_snapshot["receiver"],
            "identification": dict(root_snapshot["identification"], due_date=None),
            "currency": self._currency(move),
            "totals": totals,
            "other_currency": self._other_currency(move, totals),
            "customs": customs,
            "lines": lines,
            "references": [references.reference(move, root_snapshot)]
            + self._references(move),
            "adjustment": {
                "root_document_id": root.id,
                "root_snapshot_sha256": root.payload_digest,
                "correction": move.dte_export_correction,
                "reason": move.dte_export_reason.strip(),
            },
        }

    # Validation -----------------------------------------------------------

    def _issuer_errors(self, company):
        env = company.env
        caf_model = env["l10n_cl.caf.authorization"]
        issuer = company.partner_id
        errors = []
        if not caf_model._is_valid_vat(caf_model._normalize_vat(issuer.vat)):
            errors.append(env._("Company: enter a valid Chilean RUT."))
        if not (issuer.l10n_cl_activity_description or "").strip():
            errors.append(env._("Company: enter the SII economic activity."))
        if not (company.dte_sii_activity_code or "").strip().isdigit():
            errors.append(env._("Company: enter a valid SII activity code."))
        if not (issuer.street or "").strip() or not (issuer.city or "").strip():
            errors.append(env._("Company: enter the DTE origin address and commune."))
        return errors

    def _receiver_errors(self, move):
        env = move.env
        receiver = move.partner_id.commercial_partner_id
        errors = []
        if not (receiver.name or "").strip():
            errors.append(env._("Customer: enter the recipient name."))
        if not receiver.country_id.l10n_cl_customs_code:
            errors.append(env._("Customer: enter a country with a customs code."))
        if not (move.dte_export_receiver_id or "").strip():
            errors.append(env._("Export: enter the foreign recipient ID."))
        if not move.dte_export_nationality_id.l10n_cl_customs_code:
            errors.append(
                env._("Export: enter the recipient nationality with a customs code.")
            )
        return errors

    def _customs_errors(self, move):
        env = move.env
        service = move.dte_export_service
        if not service:
            return [env._("Export: choose goods or the exported service.")]
        required = GOODS_REQUIRED if service == "goods" else SERVICE_REQUIRED[service]
        errors = [
            env._("Export: enter the %(field)s.") % {"field": label}
            for name, label in required
            if not move[name]
        ]
        destination = move.dte_export_destination_id
        if destination and not destination.l10n_cl_customs_code:
            errors.append(env._("Export: the destination needs a customs code."))
        reference = SERVICE_REFERENCE.get(service)
        if reference and reference not in move.dte_export_reference_ids.mapped(
            "document_type"
        ):
            errors.append(
                env._("Export: this service needs a %(code)s supporting reference.")
                % {"code": reference}
            )
        for weight, unit in WEIGHTS:
            if move[weight] and not move[unit]:
                errors.append(env._("Export: every weight needs its customs unit."))
        same_unit = move.dte_export_gross_unit_id == move.dte_export_net_unit_id
        if same_unit and move.dte_export_net_weight > move.dte_export_gross_weight:
            errors.append(env._("Export: the net weight exceeds the gross weight."))
        return errors

    def _currency_errors(self, move):
        env = move.env
        errors = []
        if not self._sii_currency_name(move.currency_id):
            errors.append(env._("Export: the currency has no SII name."))
        if move.currency_id.name == "CLP":
            return errors
        rate = self._decimal(move.dte_export_rate or 0)
        if not 0 < rate <= MAX_RATE:
            errors.append(env._("Export: enter the CLP exchange rate of the invoice."))
        if not move.dte_export_rate_date or not move.dte_export_rate_source:
            errors.append(env._("Export: enter the exchange rate date and its source."))
        return errors

    # Capture ----------------------------------------------------------------

    def _issuer(self, company):
        issuer = InvoiceSnapshotService()._partner_snapshot(
            company.partner_id,
            activity=company.partner_id.l10n_cl_activity_description,
            activity_code=(company.dte_sii_activity_code or "").strip(),
        )
        issuer["vat"] = self._xml_rut(company.env, issuer["vat"])
        issuer["email"] = (company.partner_id.email or "").strip() or None
        return issuer

    def _receiver(self, move):
        partner = move.partner_id.commercial_partner_id
        street = " ".join(
            part.strip() for part in (partner.street, partner.street2) if part
        )
        return {
            "vat": FOREIGN_RECEIVER_RUT,
            "name": partner.name.strip(),
            "commercial_partner_id": partner.id,
            "address": street or None,
            "city": (partner.city or "").strip() or None,
            "country_code": partner.country_id.l10n_cl_customs_code,
            "foreign_id": move.dte_export_receiver_id.strip(),
            "nationality_code": move.dte_export_nationality_id.l10n_cl_customs_code,
            "extra_id": (move.dte_export_receiver_extra_id or "").strip() or None,
        }

    def _identification(self, move):
        service = move.dte_export_service
        due = move.invoice_date_due
        return {
            "service_indicator": None if service == "goods" else service,
            "export_payment_code": move.dte_export_payment_id.code or None,
            "due_date": due.isoformat() if due and due != move.invoice_date else None,
        }

    def _currency(self, move):
        currency = move.currency_id
        return {
            "id": currency.id,
            "iso_code": currency.name,
            "sii_name": self._sii_currency_name(currency),
            "rounding": self._decimal_text(self._decimal(currency.rounding)),
        }

    def _lines(self, move):
        env = move.env
        invoice_lines = move.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        if not invoice_lines or len(invoice_lines) > MAX_DETAIL_LINES:
            raise ValidationError(
                env._("An export needs between one and 60 detail lines.")
            )
        return [
            self._line(move, line, sequence)
            for sequence, line in enumerate(invoice_lines, start=1)
        ]

    def _line(self, move, line, sequence):
        env = move.env
        if line.tax_ids.filtered(lambda tax: tax.amount):
            raise ValidationError(env._("Export lines cannot carry taxes."))
        if line.discount:
            raise ValidationError(
                env._(
                    "Export lines cannot carry a discount yet; "
                    "enter the discounted unit price."
                )
            )
        quantity = self._decimal(line.quantity)
        price = self._decimal(line.price_unit)
        if quantity <= 0 or price < 0:
            raise ValidationError(
                env._("Export lines need a positive quantity and price.")
            )
        return {
            "sequence": sequence,
            "source_line_id": line.id,
            "name": (line.product_id.name or line.name or "").strip()[:80],
            "description": (line.name or "").strip()[:1000] or None,
            "quantity": self._decimal_text(
                quantity.quantize(QUANTITY_DIGITS, rounding=ROUND_HALF_UP).normalize()
            ),
            "uom": (line.product_uom_id.name or "")[:4] or None,
            "unit_price": self._decimal_text(
                price.quantize(QUANTITY_DIGITS, rounding=ROUND_HALF_UP).normalize()
            ),
            "subtotal": self._money(move, line.price_subtotal),
            "exempt": True,
        }

    def _totals(self, move, lines, allow_zero=False):
        total = sum((Decimal(line["subtotal"]) for line in lines), Decimal(0))
        untaxed = Decimal(self._money(move, move.amount_untaxed))
        if total != untaxed or untaxed != Decimal(self._money(move, move.amount_total)):
            raise ValidationError(
                move.env._("The export totals do not reconcile with its lines.")
            )
        if total < 0 or (total == 0 and not allow_zero):
            raise ValidationError(move.env._("An export total must be positive."))
        return {"exempt": self._decimal_text(total), "total": self._decimal_text(total)}

    def _other_currency(self, move, totals):
        rate = (
            Decimal(1)
            if move.currency_id.name == "CLP"
            else self._decimal(move.dte_export_rate).quantize(RATE_DIGITS)
        )
        clp_total = (Decimal(totals["total"]) * rate).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )
        return {
            "sii_name": CLP_SII_NAME,
            "rate": self._decimal_text(rate),
            "rate_date": (
                move.dte_export_rate_date.isoformat()
                if move.dte_export_rate_date
                else None
            ),
            "rate_source": move.dte_export_rate_source or None,
            "exempt": str(clp_total),
            "total": str(clp_total),
        }

    def _customs(self, move, lines):
        def code(record):
            return record.code if record else None

        def amount(value):
            return self._money(move, value) if value else None

        def weight(value):
            return self._decimal_text(self._decimal(value)) if value else None

        packages = move.dte_export_package_ids
        carrier = move.dte_export_carrier_vat
        return {
            "sale_mode": code(move.dte_export_sale_mode_id),
            "sale_clause": code(move.dte_export_sale_clause_id),
            "clause_total": amount(move.dte_export_clause_total),
            "transport_mode": code(move.dte_export_transport_id),
            "transport_name": move.dte_export_transport_name or None,
            "transport_extra": move.dte_export_transport_extra or None,
            "carrier_vat": self._xml_rut(move.env, carrier) if carrier else None,
            "carrier_name": move.dte_export_carrier_name or None,
            "booking": move.dte_export_booking or None,
            "operator": move.dte_export_operator or None,
            "loading_port": code(move.dte_export_loading_port_id),
            "loading_extra": move.dte_export_loading_extra or None,
            "unloading_port": code(move.dte_export_unloading_port_id),
            "unloading_extra": move.dte_export_unloading_extra or None,
            "tare": str(move.dte_export_tare) if move.dte_export_tare else None,
            "tare_unit": code(move.dte_export_tare_unit_id),
            "gross_weight": weight(move.dte_export_gross_weight),
            "gross_unit": code(move.dte_export_gross_unit_id),
            "net_weight": weight(move.dte_export_net_weight),
            "net_unit": code(move.dte_export_net_unit_id),
            "total_items": len(lines),
            "total_packages": sum(packages.mapped("quantity")) or None,
            "packages": [self._package(package) for package in packages],
            "freight": amount(move.dte_export_freight),
            "insurance": amount(move.dte_export_insurance),
            "recipient_country": (
                move.partner_id.commercial_partner_id.country_id.l10n_cl_customs_code
            ),
            "destination_country": (
                move.dte_export_destination_id.l10n_cl_customs_code or None
            ),
        }

    def _package(self, package):
        return {
            "type": package.type_id.code,
            "quantity": package.quantity,
            "marks": package.marks or None,
            "container": package.container or None,
            "seal": package.seal or None,
            "seal_issuer": package.seal_issuer or None,
        }

    def _references(self, move):
        return [
            {
                "document_type": reference.document_type,
                "folio": reference.folio,
                "date": reference.date.isoformat(),
                "reason": reference.reason or None,
            }
            for reference in move.dte_export_reference_ids
        ]

    # Helpers ----------------------------------------------------------------

    def _sii_currency_name(self, currency):
        return (currency.with_context(lang="en_US").l10n_cl_short_name or "").strip()

    def _money(self, move, value):
        digits = self._decimal(move.currency_id.rounding).normalize()
        if digits < RATE_DIGITS:
            raise ValidationError(
                move.env._("The currency has more than four decimal places.")
            )
        amount = self._decimal(value).quantize(digits, rounding=ROUND_HALF_UP)
        return self._decimal_text(amount)

    def _xml_rut(self, env, vat):
        normalized = env["l10n_cl.caf.authorization"]._normalize_vat(vat)
        return f"{int(normalized[:-1])}-{normalized[-1]}"

    def _decimal(self, value):
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise ValidationError(_lt("Export amounts must be numeric.")) from error
        if not amount.is_finite():
            raise ValidationError(_lt("Export amounts must be finite."))
        return amount

    def _decimal_text(self, value):
        return format(value, "f")
