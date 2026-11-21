# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from decimal import Decimal

from odoo.addons.l10n_cl_dte_inbox_account.services.common import (
    canonical_folio,
    decimal,
    reject,
    vat,
)

from .common import PURCHASE_CODE, SALE_MOVE_TYPE, reject_received


class ReceivedPurchaseComparisonService:
    """Prove that a customer invoice books exactly the signed DTE 46."""

    def __init__(self, env):
        self.env = env

    def _equal(self, source, actual, ordinal=0):
        if decimal(source) != decimal(actual):
            reject(
                self.env,
                "amount_mismatch",
                ordinal=ordinal,
                source_value=source,
                odoo_value=actual,
            )

    def preview(self, document, mapped_lines, config):
        """Compute the native taxes of the mapped details and match the XML."""
        company = config["journal"].company_id
        currency = company.currency_id
        tax_model = self.env["account.tax"]
        bases = []
        for mapped in mapped_lines:
            line = mapped["source"]
            for source in (
                line["quantity"],
                line["unit_price"],
                line["discount_percent"],
            ):
                self._equal(source, float(source), line["ordinal"])
            bases.append(
                tax_model._prepare_base_line_for_taxes_computation(
                    {},
                    currency_id=currency,
                    company_id=company,
                    price_unit=float(line["unit_price"]),
                    quantity=float(line["quantity"]),
                    discount=float(line["discount_percent"]),
                    tax_ids=self.line_taxes(line, config),
                )
            )
        tax_model._add_tax_details_in_base_lines(bases, company)
        tax_model._round_base_lines_tax_details(bases, company)
        totals = dict(net=Decimal(0), exempt=Decimal(0), vat=Decimal(0))
        totals["retention"] = Decimal(0)
        for mapped, base in zip(mapped_lines, bases, strict=True):
            line = mapped["source"]
            amount = currency.round(base["tax_details"]["total_excluded_currency"])
            self._equal(line["amount"], amount, line["ordinal"])
            totals["exempt" if line["exempt"] else "net"] += decimal(amount)
            for tax_data in base["tax_details"]["taxes_data"]:
                key = "vat" if tax_data["tax"] == config["vat_tax"] else "retention"
                totals[key] += decimal(tax_data["tax_amount_currency"])
        summary = tax_model._get_tax_totals_summary(bases, currency, company)
        self._check_totals(document, totals, summary["total_amount_currency"])
        return totals

    def _check_totals(self, document, totals, total):
        source = document["totals"]
        retained = sum(decimal(item["amount"]) for item in source["retentions"])
        self._equal(source["net"], totals["net"])
        self._equal(source["exempt"], totals["exempt"])
        self._equal(source["vat"], totals["vat"])
        self._equal(-retained, totals["retention"])
        self._equal(source["total"], total)

    @staticmethod
    def line_taxes(line, config):
        """Taxable details declare sales VAT and the buyer's total retention."""
        if line["exempt"]:
            return config["vat_tax"].browse()
        return config["vat_tax"] | config["retention_tax"]

    def compare_move(self, document, move, config, line_map=None):
        """Compare the header, every detail and the journal items."""
        move.ensure_one()
        move.check_access("read")
        self._compare_header(document, move)
        details = move.invoice_line_ids.filtered(
            lambda detail: detail.display_type == "product"
        )
        line_map = line_map or {line.dte_inbox_line_number: line.id for line in details}
        if (
            len(details) != len(document["lines"])
            or set(line_map) != {line["ordinal"] for line in document["lines"]}
            or set(line_map.values()) != set(details.ids)
        ):
            reject_received(self.env, "invoice_incompatible")
        mapped = []
        for source in document["lines"]:
            line = details.browse(line_map[source["ordinal"]])
            for key, value in (
                ("quantity", line.quantity),
                ("unit_price", line.price_unit),
                ("discount_percent", line.discount),
                ("amount", line.price_subtotal),
            ):
                self._equal(source[key], value, source["ordinal"])
            if line.tax_ids != self.line_taxes(source, config):
                reject_received(self.env, "tax_invalid", ordinal=source["ordinal"])
            mapped.append(dict(source=source))
        self.preview(document, mapped, config)
        self._compare_entry(document, move, config)
        return line_map

    def _compare_header(self, document, move):
        identity = document["identity"]
        if (
            move.company_id != self.env.company
            or move.move_type != SALE_MOVE_TYPE
            or vat(move.commercial_partner_id.vat) != identity["issuer_vat"]
            or move.l10n_latam_document_type_id.code != PURCHASE_CODE
            or canonical_folio(move.l10n_latam_document_number) != identity["folio"]
            or str(move.invoice_date) != identity["issue_date"]
            or move.currency_id.name != "CLP"
            or (
                identity["due_date"]
                and str(move.invoice_date_due) != identity["due_date"]
            )
        ):
            reject_received(self.env, "invoice_incompatible")

    def _compare_entry(self, document, move, config):
        """Receivable is MntTotal; VAT is declared and the retention credited."""
        source = document["totals"]
        retained = sum(decimal(item["amount"]) for item in source["retentions"])
        lines = move.line_ids
        receivable = lines.filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
        )
        self._equal(source["total"], move.amount_total)
        self._equal(source["total"], sum(receivable.mapped("balance")))
        self._equal(
            -decimal(source["vat"]),
            sum(
                lines.filtered(
                    lambda line: line.tax_line_id == config["vat_tax"]
                ).mapped("balance")
            ),
        )
        self._equal(
            retained,
            sum(
                lines.filtered(
                    lambda line: line.tax_line_id == config["retention_tax"]
                ).mapped("balance")
            ),
        )
        self._equal(
            -(decimal(source["net"]) + decimal(source["exempt"])),
            sum(
                lines.filtered(lambda line: line.display_type == "product").mapped(
                    "balance"
                )
            ),
        )
