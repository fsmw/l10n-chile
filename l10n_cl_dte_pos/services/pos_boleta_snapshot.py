# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from decimal import Decimal

from odoo import fields
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_boleta.services.boleta_snapshot import (
    BoletaSnapshotService,
)
from odoo.addons.l10n_cl_dte_xml.services.invoice_snapshot import (
    InvoiceSnapshotService,
)


class PosBoletaSnapshotService:
    """Build the canonical immutable 39/41 snapshot from a POS order."""

    def __init__(self):
        self.boleta_service = BoletaSnapshotService()
        self.invoice_service = InvoiceSnapshotService()

    def validate_source(self, order, document_code):
        order.ensure_one()
        if order._name != "pos.order":
            raise ValidationError(order.env._("A persisted POS order is required."))
        if str(document_code) not in {"39", "41"}:
            raise ValidationError(
                order.env._("Only Chilean boletas 39 and 41 are supported.")
            )
        if order.state != "paid" or not order.date_order:
            raise ValidationError(
                order.env._("Only paid, persisted POS orders can issue DTEs.")
            )
        if order.session_id.dte_pos_accounting_mode != "session_summary":
            raise ValidationError(
                order.env._("Direct POS DTEs require session summary mode.")
            )
        if order.company_id.country_id.code != "CL":
            raise ValidationError(
                order.env._("The company must be registered in Chile.")
            )
        if order.currency_id.name != "CLP":
            raise ValidationError(
                order.env._("Electronic boletas must be issued in CLP.")
            )
        if order.company_id.dte_sii_environment != "demo":
            raise ValidationError(
                order.env._(
                    "POS boleta issuance is currently available only in Demo mode."
                )
            )
        if order.amount_total <= 0:
            raise ValidationError(
                order.env._("Refunds and zero-value orders are not supported.")
            )
        if order.account_move:
            raise ValidationError(
                order.env._(
                    "A direct-source POS boleta cannot be linked to an invoice."
                )
            )
        self._validate_issuer(order.company_id)
        receiver = order.partner_id.commercial_partner_id if order.partner_id else False
        if receiver and not (receiver.name or "").strip():
            raise ValidationError(order.env._("The POS receiver must have a name."))
        if receiver and not (receiver.vat or "").strip():
            raise ValidationError(
                order.env._("Set a receiver RUT or use the generic final-consumer RUT.")
            )
        if receiver and not self.boleta_service._is_generic_receiver_rut(receiver.vat):
            caf_model = order.env["l10n_cl.caf.authorization"]
            if not caf_model._is_valid_vat(caf_model._normalize_vat(receiver.vat)):
                raise ValidationError(
                    order.env._("Enter a valid Chilean receiver RUT.")
                )
        lines, taxable_net, _exempt_net = self._snapshot_lines(order)
        if not lines:
            raise ValidationError(
                order.env._("The POS order needs at least one product line.")
            )
        if str(document_code) == "41" and taxable_net:
            raise ValidationError(
                order.env._("Boleta type 41 requires all lines to be exempt.")
            )
        self._payment_method_for_order(order, required=False)
        return True

    def capture(self, order, caf, folio_number, document_code):
        """Capture partner, lines, taxes, payments, session, and totals."""
        self.validate_source(order, document_code)
        if (
            caf.document_code != str(document_code)
            or caf.company_id != order.company_id
        ):
            raise ValidationError(
                order.env._("The CAF must match the POS company and DTE type.")
            )
        caf._check_caf_integrity()
        lines, taxable_net, exempt_net = self._snapshot_lines(order)
        total = self.boleta_service._integer(order.amount_total, order.env._("total"))
        tax = self.boleta_service._integer(order.amount_tax, order.env._("VAT"))
        net = self.boleta_service._integer(
            order.amount_total - order.amount_tax, order.env._("net amount")
        )
        if taxable_net + exempt_net != net or net + tax != total:
            raise ValidationError(
                order.env._("The POS order totals do not reconcile with its lines.")
            )
        if str(document_code) == "41" and (taxable_net or tax):
            raise ValidationError(
                order.env._("Boleta type 41 can contain only exempt lines.")
            )
        receiver = order.partner_id.commercial_partner_id if order.partner_id else False
        receiver_snapshot = (
            self.invoice_service._partner_snapshot(receiver)
            if receiver
            else {
                "vat": BoletaSnapshotService.GENERIC_RECEIVER_RUT,
                "name": "Consumidor Final",
                "address": "",
                "commune": "",
                "city": "",
            }
        )
        if self.boleta_service._is_generic_receiver_rut(receiver_snapshot["vat"]):
            receiver_snapshot["vat"] = BoletaSnapshotService.GENERIC_RECEIVER_RUT
        else:
            receiver_snapshot["vat"] = self.boleta_service._xml_rut(
                receiver_snapshot["vat"]
            )
        if receiver:
            receiver_snapshot["email"] = (receiver.email or "").strip()
            receiver_snapshot["phone"] = (
                receiver.phone or receiver.mobile or ""
            ).strip()
        issuer_partner = order.company_id.partner_id
        issuer = self.invoice_service._partner_snapshot(
            issuer_partner,
            activity=order.company_id.l10n_cl_activity_description,
            activity_code=order.company_id.dte_sii_activity_code,
        )
        issuer["vat"] = self.boleta_service._xml_rut(issuer["vat"])
        total_decimal = Decimal(str(order.amount_total))
        local_dt = fields.Datetime.context_timestamp(
            order.with_context(tz="America/Santiago"), order.date_order
        )
        limit = self.boleta_service._annual_135_uf_limit_for(
            order.env, order.company_id, local_dt.date()
        )
        over_threshold = total_decimal > limit
        if over_threshold and (
            not receiver
            or self.boleta_service._is_generic_receiver_rut(receiver_snapshot["vat"])
        ):
            raise ValidationError(
                order.env._("A boleta over 135 UF requires the identified payer's RUT.")
            )
        payment_code = self._payment_method_for_order(order, required=over_threshold)
        if (
            over_threshold
            and payment_code == "1"
            and receiver
            and (
                not (receiver.email or "").strip()
                or not (receiver.phone or receiver.mobile or "").strip()
            )
        ):
            raise ValidationError(
                order.env._(
                    "Cash boletas over 135 UF require receiver email and phone."
                )
            )
        stamp = self.invoice_service._chile_timestamp()
        return {
            "company_id": order.company_id.id,
            "document_code": str(document_code),
            "folio": int(folio_number),
            "issue_date": local_dt.date().isoformat(),
            "document_timestamp": stamp,
            "ted_timestamp": stamp,
            "service_indicator": BoletaSnapshotService.SERVICE_INDICATOR,
            "net_amount_indicator": "2" if str(document_code) == "39" else None,
            "payment_method": payment_code if over_threshold else None,
            "issuer": issuer,
            "receiver": receiver_snapshot,
            "totals": {
                "net": str(int(net)) if taxable_net else None,
                "exempt": str(int(exempt_net)) if exempt_net else None,
                "vat_rate": "19" if taxable_net else None,
                "vat": str(int(tax)) if taxable_net else None,
                "total": str(int(total)),
            },
            "lines": lines,
            "pos_order_id": order.id,
            "pos_session_id": order.session_id.id,
            "pos_reference": order.pos_reference,
        }

    def _validate_issuer(self, company):
        partner = company.partner_id
        missing = []
        if not (partner.vat or "").strip():
            missing.append(company.env._("company RUT"))
        else:
            caf_model = company.env["l10n_cl.caf.authorization"]
            if not caf_model._is_valid_vat(caf_model._normalize_vat(partner.vat)):
                missing.append(company.env._("valid company RUT"))
        if not (partner.name or "").strip():
            missing.append(company.env._("company legal name"))
        if not (company.l10n_cl_activity_description or "").strip():
            missing.append(company.env._("company economic activity"))
        activity_code = (company.dte_sii_activity_code or "").strip()
        if not activity_code.isdigit() or int(activity_code or "0") <= 0:
            missing.append(company.env._("valid SII activity code"))
        if not (partner.street or "").strip() or not (partner.city or "").strip():
            missing.append(company.env._("company address and commune"))
        if missing:
            raise ValidationError(
                company.env._("Complete the following issuer data before issuing: %s")
                % ", ".join(missing)
            )

    def _snapshot_lines(self, order):
        self.boleta_service.env = order.env
        lines = order.lines.filtered(lambda line: line.product_id and line.qty > 0)
        if len(lines) > BoletaSnapshotService.MAX_DETAIL_LINES:
            raise ValidationError(
                order.env._("A boleta supports at most 1000 detail lines.")
            )
        snapshots = []
        taxable_net = Decimal("0")
        exempt_net = Decimal("0")
        for line in lines:
            taxable = self.invoice_service._is_taxable_19_percent(line.tax_ids)
            subtotal = self.boleta_service._integer(
                line.price_subtotal, order.env._("line subtotal")
            )
            if subtotal < 0:
                raise ValidationError(
                    order.env._("Negative POS order lines are not supported.")
                )
            taxable_net += subtotal if taxable else Decimal("0")
            exempt_net += Decimal("0") if taxable else subtotal
            name = (line.product_id.name or line.full_product_name or "").strip()
            description = (line.full_product_name or name).strip()
            qty = Decimal(str(line.qty))
            if not name or len(name) > 80 or len(description) > 1000 or qty <= 0:
                raise ValidationError(
                    order.env._("The POS line is not valid for a boleta.")
                )
            snapshots.append(
                {
                    "name": name,
                    "description": description,
                    "quantity": self.boleta_service._decimal_text(qty),
                    "uom": (
                        line.product_uom_id.name or line.product_id.uom_id.name or ""
                    )[:4],
                    "unit_price": self.boleta_service._decimal_text(
                        Decimal(str(line.price_unit))
                    ),
                    "discount_percent": self.boleta_service._decimal_text(
                        Decimal(str(line.discount))
                    )
                    if line.discount
                    else None,
                    "subtotal": str(int(subtotal)),
                    "exempt": not taxable,
                }
            )
        return snapshots, taxable_net, exempt_net

    def _payment_method_for_order(self, order, required):
        payments = order.payment_ids.filtered(
            lambda payment: payment.amount > 0 and not payment.is_change
        )
        codes = payments.mapped("payment_method_id.l10n_cl_dte_boleta_payment_method")
        if required and (not payments or any(not code for code in codes)):
            raise ValidationError(
                order.env._(
                    "Map every POS payment method to an SII code for boletas "
                    "over 135 UF."
                )
            )
        distinct_codes = set(codes) - {False}
        if required and len(distinct_codes) != 1:
            raise ValidationError(
                order.env._(
                    "All POS payment methods in a high-value boleta must map "
                    "to one SII code."
                )
            )
        return next(iter(distinct_codes)) if len(distinct_codes) == 1 else False
