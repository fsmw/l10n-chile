# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from decimal import Decimal

from odoo import _
from odoo.exceptions import UserError

from odoo.addons.l10n_cl_dte_account.services.dte_issuance_adapter import (
    InvoiceIssuanceAdapter,
)

from .set_parser import CertificationSetParser, decimal_value
from .synthetic_receiver_activity_service import apply_receiver_activity_to_snapshot


class CertificationInvoiceAdapter(InvoiceIssuanceAdapter):
    """Bind the source case and SET reference before common DTE signing."""

    snapshot_producer_version = "l10n_cl_dte_certification/18.0"

    def _check_case(self, move, document_code):
        move.ensure_one()
        line = move.dte_certification_run_line_id
        if not line or line.move_id != move or not line.selected:
            raise UserError(
                _("The invoice is not linked to a selected execution case.")
            )
        run = line.run_id
        run._check_execution_access()
        if not run.input_digest or run.state not in {
            "generated",
            "partial",
            "prepared",
        }:
            raise UserError(
                _("Generate and freeze the execution before preparing DTE.")
            )
        if (
            CertificationSetParser().canonical_digest(run.input_snapshot)
            != run.input_digest
        ):
            raise UserError(_("The frozen execution input has changed."))
        if run.input_snapshot["source_digest"] != run.set_id.source_digest:
            raise UserError(_("The original SII set fingerprint has changed."))
        profiles = move.env["l10n_cl.dte.signing.profile"].search(
            [
                ("company_id", "=", run.company_id.id),
                ("sii_environment", "=", "certification"),
                ("active", "=", True),
            ]
        )
        if (
            len(profiles) != 1
            or profiles.id != run.input_snapshot["signing_profile_id"]
        ):
            raise UserError(
                _("The Certification signing profile changed after generation.")
            )
        fingerprint = profiles.certificate_id._get_fingerprint_bytes(
            hashing_algorithm="sha256", formatting="raw"
        ).hex()
        if fingerprint != run.input_snapshot["signing_certificate_sha256"]:
            raise UserError(
                _("The Certification signing certificate changed after generation.")
            )
        if (
            move.company_id != run.company_id
            or move.partner_id != line.case_id.partner_id
        ):
            raise UserError(_("The invoice company or receiver differs from the case."))
        if (
            move.invoice_date != run.issue_date
            or line.case_id.document_code != document_code
        ):
            raise UserError(
                _("The invoice issue date or DTE type differs from the case.")
            )
        actual = move.invoice_line_ids.filtered(
            lambda item: item.display_type == "product"
        )
        expected = line.case_id.line_ids.sorted("sequence")
        if len(actual) != len(expected):
            raise UserError(_("The invoice detail count differs from the SII case."))
        for invoice_line, detail in zip(actual, expected, strict=True):
            if (
                invoice_line.name != detail.name
                or Decimal(str(invoice_line.quantity))
                != decimal_value(detail.quantity_text)
                or Decimal(str(invoice_line.price_unit))
                != decimal_value(detail.unit_price_text)
                or Decimal(str(invoice_line.discount))
                != decimal_value(
                    line.case_id.global_discount_percent
                    if line.case_id.global_discount_percent and not detail.exempt
                    else detail.discount_percent_text
                )
                or invoice_line.product_id != detail.product_id
            ):
                raise UserError(
                    _("An invoice detail differs from its frozen SII case.")
                )
            tax = run.set_id.exempt_tax_id if detail.exempt else run.set_id.vat_tax_id
            if invoice_line.tax_ids != tax:
                raise UserError(_("An invoice tax differs from its frozen SII case."))
        return line

    def validate_source(self, move, document_code):
        self._check_case(move, document_code)
        return super().validate_source(move, document_code)

    def capture_snapshot(self, move, caf, folio_number, document_code):
        line = self._check_case(move, document_code)
        snapshot = super().capture_snapshot(move, caf, folio_number, document_code)
        run = line.run_id
        snapshot["certification"] = {
            "set_id": run.set_id.id,
            "case_identifier": line.case_id.case_identifier,
            "revision": run.revision,
            "source_digest": run.set_id.source_digest,
            "input_digest": run.input_digest,
        }
        snapshot["references"] = [
            {
                "line_number": 1,
                "document_type": "SET",
                "folio": "1",
                "date": run.issue_date.isoformat(),
                "reason": f"CASO {line.case_id.case_identifier}",
            }
        ]
        if line.case_id.global_discount_percent:
            discount = Decimal(line.case_id.global_discount_percent)
            for source, invoice_line in zip(
                snapshot["lines"],
                move.invoice_line_ids.filtered(
                    lambda item: item.display_type == "product"
                ),
                strict=True,
            ):
                if source["exempt"]:
                    continue
                gross = Decimal(str(invoice_line.quantity)) * Decimal(
                    str(invoice_line.price_unit)
                )
                if gross != gross.to_integral_value():
                    raise UserError(
                        _("A global discount case requires integer CLP detail values.")
                    )
                source["subtotal"] = str(int(gross))
                source["discount_percent"] = None
            snapshot["global_discount"] = {
                "movement": "D",
                "description": "Descuento global",
                "value_type": "%",
                "value": format(discount.normalize(), "f"),
            }
        return apply_receiver_activity_to_snapshot(snapshot, line.case_id)
