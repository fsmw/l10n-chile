# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from decimal import Decimal

from .common import canonical_folio, decimal, digest, reject, vat
from .mapping_service import IncomingAccountMappingService


class IncomingAccountComparisonService:
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

    def preview(self, document, mapped_lines, journal):
        company = journal.company_id
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
            base = tax_model._prepare_base_line_for_taxes_computation(
                {},
                currency_id=currency,
                company_id=company,
                price_unit=float(line["unit_price"]),
                quantity=float(line["quantity"]),
                discount=float(line["discount_percent"]),
                tax_ids=self.env["account.tax"].browse(mapped["tax_id"]),
            )
            bases.append(base)
            if line["discount_amount"] is not None:
                expected = currency.round(
                    float(
                        decimal(line["quantity"])
                        * decimal(line["unit_price"])
                        * decimal(line["discount_percent"])
                        / 100
                    )
                )
                self._equal(line["discount_amount"], expected, line["ordinal"])
        tax_model._add_tax_details_in_base_lines(bases, company)
        tax_model._round_base_lines_tax_details(bases, company)
        net, exempt = Decimal(0), Decimal(0)
        for mapped, base in zip(mapped_lines, bases, strict=True):
            amount = base["tax_details"]["total_excluded_currency"]
            line = mapped["source"]
            self._equal(line["amount"], currency.round(amount), line["ordinal"])
            if line["exempt"]:
                exempt += decimal(currency.round(amount))
            else:
                net += decimal(currency.round(amount))
        summary = tax_model._get_tax_totals_summary(bases, currency, company)
        actual = dict(
            net=str(net),
            exempt=str(exempt),
            vat=str(summary["tax_amount_currency"]),
            total=str(summary["total_amount_currency"]),
        )
        for key, value in actual.items():
            self._equal(document["totals"][key], value)
        return actual

    def compare_move(self, document, move, line_map=None):
        move.ensure_one()
        move.check_access("read")
        identity = document["identity"]
        if (
            move.company_id != self.env.company
            or move.move_type != "in_invoice"
            or vat(move.commercial_partner_id.vat) != identity["issuer_vat"]
            or move.l10n_latam_document_type_id.code != identity["document_code"]
            or canonical_folio(move.l10n_latam_document_number) != identity["folio"]
            or str(move.invoice_date) != identity["issue_date"]
            or move.currency_id.name != "CLP"
            or (
                identity["due_date"]
                and str(move.invoice_date_due) != identity["due_date"]
            )
        ):
            reject(self.env, "existing_bill_incompatible")
        details = move.invoice_line_ids.filtered(
            lambda detail: detail.display_type == "product"
        )
        if len(details) != len(document["lines"]):
            reject(self.env, "existing_bill_incompatible")
        line_map = line_map or {line.dte_inbox_line_number: line.id for line in details}
        if set(line_map) != {detail["ordinal"] for detail in document["lines"]} or set(
            line_map.values()
        ) != set(details.ids):
            reject(self.env, "existing_bill_incompatible")
        mapped = []
        for source in document["lines"]:
            line = details.filtered(
                lambda detail, ordinal=source["ordinal"]: detail.id == line_map[ordinal]
            )
            for key, value in (
                ("quantity", line.quantity),
                ("unit_price", line.price_unit),
                ("discount_percent", line.discount),
                ("amount", line.price_subtotal),
            ):
                self._equal(source[key], value, source["ordinal"])
            if len(line.tax_ids) != 1:
                reject(self.env, "tax_invalid", ordinal=source["ordinal"])
            values = dict(
                account_id=line.account_id.id,
                tax_id=line.tax_ids.id,
                product_id=line.product_id.id,
                uom_id=line.product_uom_id.id,
            )
            IncomingAccountMappingService(self.env)._validate_line(
                source, values, move.company_id
            )
            mapped.append(dict(source=source, **values))
        self.preview(document, mapped, move.journal_id)
        self._equal(document["totals"]["total"], move.amount_total)
        self._equal(document["totals"]["vat"], move.amount_tax)
        self._equal(
            decimal(document["totals"]["net"]) + decimal(document["totals"]["exempt"]),
            move.amount_untaxed,
        )
        payable = move.line_ids.filtered(
            lambda line: line.account_id.account_type == "liability_payable"
        )
        self._equal(
            -decimal(document["totals"]["total"]), sum(payable.mapped("balance"))
        )
        self._equal(
            document["totals"]["vat"],
            sum(move.line_ids.filtered("tax_line_id").mapped("balance")),
        )
        return line_map

    def preview_digest(self, document, overrides, target=None):
        company = self.env.company
        records = []
        for choice in overrides.values():
            for key, model in (
                ("account_id", "account.account"),
                ("tax_id", "account.tax"),
                ("product_id", "product.product"),
                ("uom_id", "uom.uom"),
                ("mapping_id", "l10n_cl.incoming.dte.account.mapping"),
            ):
                record = self.env[model].browse(choice.get(key)).exists()
                if record:
                    record.check_access("read")
                    records.append([model, record.id, str(record.write_date)])
        return digest(
            dict(
                document=document,
                choices=overrides,
                records=sorted(records),
                company=[
                    company.id,
                    company.dte_sii_environment,
                    company.dte_inbox_account_enabled,
                    str(company.write_date),
                    company.tax_calculation_rounding_method,
                    company.currency_id.rounding,
                ],
                target=[
                    target.id,
                    str(target.write_date),
                    target.state,
                    target.amount_total,
                    [
                        (
                            detail.id,
                            str(detail.write_date),
                            detail.quantity,
                            detail.price_unit,
                            detail.discount,
                            detail.account_id.id,
                            detail.tax_ids.ids,
                            detail.product_id.id,
                            detail.product_uom_id.id,
                        )
                        for detail in target.invoice_line_ids
                    ],
                ]
                if target
                else None,
            )
        )
