# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Compare recovered invoice VAT when tax tag versions differ."""

from odoo import models

from odoo.addons.l10n_cl_dte_mipyme_recovery.services.identity import (
    normalize_folio,
    normalize_rut,
)


class RcvMatcher(models.AbstractModel):
    _inherit = "l10n.cl.rcv.matcher"

    def _candidate_moves(self, document):
        if self._is_generic_export(document):
            # The base matcher includes native and imported invoices with the
            # same fiscal identity. Recovery evidence must not hide duplicates.
            return super()._candidate_moves(document)
        if document.operation != "sale" or document.document_type_code not in (
            "110",
            "111",
        ):
            return super()._candidate_moves(document)
        try:
            issuer = normalize_rut(document.issuer_vat)
            folio = normalize_folio(document.folio)
        except ValueError:
            return self.env["account.move"]
        recoveries = self.env["l10n_cl.dte.recovery"].search(
            [
                ("company_id", "=", document.company_id.id),
                ("issuer_rut", "=", issuer),
                ("dte_type", "=", document.document_type_code),
                ("folio", "=", folio),
                ("move_id", "!=", False),
            ]
        )
        return recoveries.mapped("move_id").filtered(
            lambda move: (
                move.state != "cancel"
                and move.move_type == "out_invoice"
                and move.company_id == document.company_id
                and move.l10n_latam_document_type_id.code == document.document_type_code
                and normalize_folio(move.l10n_latam_document_number) == folio
            )
        )

    def _reconcile(self, documents):
        result = super()._reconcile(documents)
        for document in documents.filtered(
            lambda item: (
                item.reconciliation_state == "amount_diff"
                and not self._is_generic_export(item)
                and (
                    item.move_id.l10n_cl_mipyme_imported
                    or item.document_type_code in ("110", "111")
                )
                and item.move_id.move_type == "out_invoice"
            )
        ):
            move = document.move_id
            exempt = self._move_exempt_amount(move)
            factor = (
                abs(move.amount_total_signed / move.amount_total)
                if move.currency_id != document.currency_id and move.amount_total
                else 1.0
            )
            checks = (
                (document.net_amount, (abs(move.amount_untaxed) - exempt) * factor),
                (document.exempt_amount, exempt * factor),
                (document.total_amount, abs(move.amount_total) * factor),
            )
            tax_state, tax_diff = self._compare_tax_components(document, move)
            if not tax_diff and all(
                document.currency_id.is_zero(expected - actual)
                for expected, actual in checks
            ):
                document.reconciliation_state = (
                    "review" if tax_state == "not_comparable" else "matched"
                )
        return result

    def _move_exempt_amount(self, move):
        if move.move_type == "out_invoice" and (
            move.l10n_cl_mipyme_imported
            or move.l10n_latam_document_type_id.code in ("110", "111")
        ):
            return sum(
                abs(line.price_subtotal)
                for line in move.invoice_line_ids
                if not line.tax_ids
            )
        return super()._move_exempt_amount(move)

    def _tagged_tax_amount(self, move, xmlid):
        amount = super()._tagged_tax_amount(move, xmlid)
        if (
            move.l10n_cl_mipyme_imported
            and move.move_type == "out_invoice"
            and xmlid == "l10n_cl.tag_cl_sale_mnt_iva"
            and amount is not None
            and move.currency_id.is_zero(amount)
        ):
            # The configured CL sale tax can use +IVA Debito Fiscal while the
            # base RCV matcher looks for the legacy Ventas - Amount VAT tag.
            # Imported invoices have already been checked against XML IVA.
            return abs(move.amount_tax)
        return amount
