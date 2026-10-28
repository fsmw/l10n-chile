# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Shared fiscal amount and tax-line classification helpers for RCV."""

from odoo import api, models


class RcvFiscalEvidence(models.AbstractModel):
    _name = "l10n.cl.rcv.fiscal.evidence"
    _description = "RCV fiscal amounts and tax evidence"

    _VAT_SII_CODE = 14
    _PURCHASE_COMPONENT_TAGS = (
        "l10n_cl.tag_cl_purchase_mnt_iva",
        "l10n_cl.tag_cl_purchase_mnt_iva_no_rec",
        "l10n_cl.tag_cl_purchase_mnt_iva_uso_comun",
    )
    _PURCHASE_COMPONENT_ALIASES = {
        "l10n_cl.tag_cl_purchase_mnt_iva": ("IVA Pagado Compras Recuperables",),
        "l10n_cl.tag_cl_purchase_mnt_iva_no_rec": ("IVA Pagado No Recuperable",),
        "l10n_cl.tag_cl_purchase_mnt_iva_uso_comun": ("IVA Pagado Compras Uso Común",),
    }

    @api.model
    def rcv_amounts(self, document):
        """Return RCV fiscal components without inferring missing values.

        ``complete`` comes from the source completeness flag, not from the
        monetary fields' default zero values. ``fiscal_vat`` and
        ``unexplained_residual`` are ``None`` when the source breakdown is
        incomplete. The residual is deliberately not classified as VAT.
        """
        complete = bool(document.tax_breakdown_available)
        components = {
            "recoverable": document.vat_amount,
            "nonrecoverable": document.vat_not_recoverable,
            "common": document.common_vat,
        }
        fiscal_vat = sum(components.values()) if complete else None
        residual = (
            document.total_amount
            - document.net_amount
            - document.exempt_amount
            - fiscal_vat
            if complete
            else None
        )
        return {
            "complete": complete,
            "components": components,
            "fiscal_vat": fiscal_vat,
            "unexplained_residual": residual,
        }

    @api.model
    def rcv_fiscal_vat(self, document):
        """Return recoverable + nonrecoverable + common-use RCV VAT.

        ``None`` means the source did not provide a complete breakdown; an
        explicit zero remains numeric zero.
        """
        return self.rcv_amounts(document)["fiscal_vat"]

    @api.model
    def rcv_unexplained_residual(self, document, identified_other_taxes=0):
        """Return an unclassified total residual, never treating it as VAT."""
        values = self.rcv_amounts(document)
        if not values["complete"]:
            return None
        return values["unexplained_residual"] - identified_other_taxes

    @api.model
    def _vat_tax_lines(self, move):
        """Return effective SII VAT tax lines for this move's direction.

        Tax identity is the configured SII code and effective tax
        repartition, not translated tag names. ``tax_line_ids`` already
        represents computed tax lines, so each accounting line is selected
        once even when it carries multiple reporting tags.
        """
        refund = move.move_type in ("in_refund", "out_refund")
        expected_document_type = "refund" if refund else "invoice"
        return move.line_ids.filtered(
            lambda line: (
                line.tax_line_id
                and line.company_id == move.company_id
                and line.tax_line_id.company_id == move.company_id
                and line.tax_line_id.l10n_cl_sii_code == self._VAT_SII_CODE
                and line.tax_repartition_line_id
                and line.tax_repartition_line_id.repartition_type == "tax"
                and line.tax_repartition_line_id.document_type == expected_document_type
            )
        )

    @api.model
    def odoo_fiscal_vat(self, move):
        """Return normalized fiscal VAT or ``None`` when tax mapping is unknown.

        Balances are summed with their accounting signs first, then normalized
        using Odoo's document direction. This preserves signed corrections and
        prevents several reporting tags from counting a tax line repeatedly.
        """
        lines = self._vat_tax_lines(move)
        if not lines:
            mapped_vat = move.invoice_line_ids.mapped("tax_ids").filtered(
                lambda tax: (
                    tax.company_id == move.company_id
                    and tax.l10n_cl_sii_code == self._VAT_SII_CODE
                )
            )
            if mapped_vat or move.currency_id.is_zero(move.amount_tax):
                return 0.0
            return None
        # Odoo's signed tax amount is the inverse of the tax-line balance sum.
        # RCV publishes document tax magnitudes, so normalize the aggregate
        # after preserving all compensating signed lines.
        return abs(-sum(lines.mapped("balance")))

    @api.model
    def tax_component_amount(self, move, tag_xmlid):
        """Return a known purchase VAT allocation component or ``None``.

        An absent tag means zero only when every effective VAT line is
        classified by one of the stable component tags. Unknown or native
        reversal tags remain not-comparable instead of becoming zero.
        """
        requested_tag = self.env.ref(tag_xmlid, raise_if_not_found=False)
        if not requested_tag:
            return None
        lines = self._vat_tax_lines(move)
        if not lines:
            return None
        selected = lines.filtered(lambda line: requested_tag in line.tax_tag_ids)
        if selected:
            return abs(-sum(selected.mapped("balance")))

        stable_tags = self.env["account.account.tag"]
        for xmlid in self._PURCHASE_COMPONENT_TAGS:
            tag = self.env.ref(xmlid, raise_if_not_found=False)
            if tag:
                stable_tags |= tag
        if lines and all(line.tax_tag_ids & stable_tags for line in lines):
            return 0.0
        return None

    @api.model
    def find_component_tax(self, company, tag_xmlid):
        """Find the unique configured purchase tax for a stable RCV component.

        The lookup intentionally ignores translated tag labels. It uses the
        exact fiscal tag on the invoice tax repartition and refuses ambiguous
        mappings.
        """
        tag = self.env.ref(tag_xmlid, raise_if_not_found=False)
        if not tag:
            return self.env["account.tax"]
        taxes = (
            self.env["account.tax"]
            .with_company(company)
            .search(
                [
                    ("company_id", "=", company.id),
                    ("type_tax_use", "=", "purchase"),
                    ("amount_type", "=", "percent"),
                    ("amount", ">", 0),
                    ("active", "=", True),
                ]
            )
        )
        aliases = self._PURCHASE_COMPONENT_ALIASES.get(tag_xmlid, ())

        def has_component(tax):
            repartitions = (
                tax.invoice_repartition_line_ids | tax.refund_repartition_line_ids
            ).filtered(
                lambda line: line.repartition_type == "tax"
                and line.document_type in ("invoice", "refund")
            )
            for repartition in repartitions:
                if tag in repartition.tag_ids:
                    return True
                sign = "+" if repartition.document_type == "invoice" else "-"
                if any(
                    candidate.name == sign + alias
                    for candidate in repartition.tag_ids
                    for alias in aliases
                ):
                    return True
            return False

        matches = taxes.filtered(lambda tax: has_component(tax))
        return matches if len(matches) == 1 else self.env["account.tax"]
