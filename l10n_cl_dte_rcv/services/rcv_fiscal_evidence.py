# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Fiscal amounts of an RCV document and the VAT booked on a journal entry."""

VAT_SII_CODE = 14
REFUND_MOVE_TYPES = ("in_refund", "out_refund")
# Purchase VAT components: RCV field, l10n_cl tag and l10n_cl tax report
# expression whose tax tags classify the component.
PURCHASE_VAT_COMPONENTS = (
    (
        "vat_amount",
        "l10n_cl.tag_cl_purchase_mnt_iva",
        "l10n_cl.tax_report_compras_iva_recup_tag",
    ),
    (
        "vat_not_recoverable",
        "l10n_cl.tag_cl_purchase_mnt_iva_no_rec",
        "l10n_cl.tax_report_compras_iva_no_recup_tag",
    ),
    (
        "common_vat",
        "l10n_cl.tag_cl_purchase_mnt_iva_uso_comun",
        "l10n_cl.tax_report_compras_iva_uso_comun_tag",
    ),
)
EXEMPT_DOCUMENT_CODE = "34"


def rcv_amounts(document):
    """Return the RCV fiscal components without inferring missing values.

    ``complete`` comes from the read's breakdown flag, not from the default
    zero amounts. ``fiscal_vat`` and ``unexplained_residual`` are ``None``
    when the breakdown is incomplete; the residual is never taken as VAT.
    """
    complete = bool(document.tax_breakdown_available)
    components = {
        "recoverable": document.vat_amount,
        "not_recoverable": document.vat_not_recoverable,
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


class RcvFiscalEvidence:
    """Read the VAT and the exempt base booked on a journal entry."""

    def __init__(self, env):
        self.env = env

    def vat_tax_lines(self, move):
        """Return the SII VAT (code 14) tax lines of ``move`` for its direction."""
        document_type = "refund" if move.move_type in REFUND_MOVE_TYPES else "invoice"
        return move.line_ids.filtered(
            lambda line: (
                line.tax_line_id
                and line.tax_line_id.company_id == move.company_id
                and line.tax_line_id.l10n_cl_sii_code == VAT_SII_CODE
                and line.tax_repartition_line_id.repartition_type == "tax"
                and line.tax_repartition_line_id.document_type == document_type
            )
        )

    def odoo_fiscal_vat(self, move):
        """Return the booked VAT, or ``None`` when its taxes are not identified.

        Signed balances are summed first and normalized afterwards, so
        compensating lines are kept and no line is counted twice.
        """
        lines = self.vat_tax_lines(move)
        if lines:
            return abs(sum(lines.mapped("balance")))
        vat_taxes = move.invoice_line_ids.tax_ids.filtered(
            lambda tax: tax.l10n_cl_sii_code == VAT_SII_CODE
        )
        if vat_taxes or move.currency_id.is_zero(move.amount_tax):
            return 0.0
        return None

    def component_tags(self, tag_xmlid, expression_xmlid):
        """Return the tax tags that classify one purchase VAT component.

        Tags and tax report expressions are configuration shared by every
        company; they are read with superuser rights.
        """
        env = self.env(su=True)
        tags = env["account.account.tag"]
        tag = env.ref(tag_xmlid, raise_if_not_found=False)
        if tag:
            tags |= tag
        expression = env.ref(expression_xmlid, raise_if_not_found=False)
        if expression and expression.engine == "tax_tags":
            tags |= tags._get_tax_tags(
                expression.formula, expression.report_line_id.report_id.country_id.id
            )
        return tags

    def tax_component_amounts(self, move):
        """Return the booked amount of each purchase VAT component.

        A component without lines is zero only when every VAT line carries a
        known component tag; otherwise the result is ``None``.
        """
        lines = self.vat_tax_lines(move)
        if not lines:
            return None
        amounts, known_tags = {}, self.env["account.account.tag"]
        for field_name, tag_xmlid, expression_xmlid in PURCHASE_VAT_COMPONENTS:
            tags = self.component_tags(tag_xmlid, expression_xmlid)
            known_tags |= tags
            selected = lines.filtered(
                lambda line, tags=tags: line.sudo().tax_tag_ids & tags
            )
            amounts[field_name] = abs(sum(selected.mapped("balance")))
        if any(not line.sudo().tax_tag_ids & known_tags for line in lines):
            return None
        return amounts

    def move_exempt_amount(self, move):
        """Return the exempt base booked on ``move``, in company currency."""
        product_lines = move.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        if (
            move.move_type == "out_invoice"
            and move.l10n_latam_document_type_id.code == EXEMPT_DOCUMENT_CODE
        ):
            exempt_lines = product_lines.filtered(
                lambda line: all(not tax.amount for tax in line.tax_ids)
            )
        else:
            exempt_lines = product_lines.filtered(
                lambda line: any(not tax.amount for tax in line.tax_ids)
            )
        return sum(abs(line.balance) for line in exempt_lines)

    def move_has_nonzero_taxes(self, move):
        """Tell whether the configured or the booked taxes of ``move`` are not zero."""
        product_lines = move.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        taxes = product_lines.tax_ids.flatten_taxes_hierarchy()
        if any(tax.amount for tax in taxes):
            return True
        return any(
            not move.company_currency_id.is_zero(line.balance)
            for line in move.line_ids.filtered("tax_line_id")
        )
