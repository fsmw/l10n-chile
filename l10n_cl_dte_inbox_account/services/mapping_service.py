# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import MAPPING, decimal, reject


class IncomingAccountMappingService:
    def __init__(self, env):
        self.env = env

    def resolve(self, document, partner, overrides=None, validate=True):
        company = self.env.company
        mappings = self.env[MAPPING].search(
            [("company_id", "=", company.id), ("partner_id", "=", partner.id)]
        )
        overrides = overrides or {}
        result = []
        for line in document["lines"]:
            matches = mappings.filtered(
                lambda m, line=line: (
                    m.match_kind == "code"
                    and any(
                        m.source_code_type == code["type"]
                        and m.source_value == code["value"]
                        for code in line["codes"]
                    )
                )
            )
            if len(matches) > 1:
                reject(self.env, "mapping_ambiguous", ordinal=line["ordinal"])
            if not matches:
                matches = mappings.filtered(
                    lambda m, line=line: (
                        m.match_kind == "description"
                        and " ".join((m.source_value or "").split())
                        == " ".join(line["name"].split())
                    )
                )
            if not matches:
                matches = mappings.filtered(lambda m: m.match_kind == "default")
            mapping = matches[:1]
            choice = overrides.get(line["ordinal"], {})
            defaults = dict(
                product_id=mapping.product_id.id,
                account_id=mapping.account_id.id,
                tax_id=mapping.tax_id.id
                or (
                    company.dte_inbox_account_exempt_tax_id.id
                    if line["exempt"]
                    else company.dte_inbox_account_vat_tax_id.id
                ),
                uom_id=mapping.uom_id.id,
            )
            values = {key: choice.get(key, value) for key, value in defaults.items()}
            values.update(
                mapping_id=mapping.id,
                existing_line_id=choice.get("existing_line_id", False),
            )
            if (
                line["unit"]
                and "uom_id" not in choice
                and (not mapping or mapping.source_uom != line["unit"])
            ):
                values["uom_id"] = False
            product = self.env["product.product"].browse(values["product_id"]).exists()
            if not values["uom_id"] and product and not line["unit"]:
                values["uom_id"] = product.uom_id.id
            if validate:
                self._validate_line(line, values, company)
            result.append(dict(source=line, **values))
        return result

    def _validate_line(self, document_line, values, company):
        ordinal = document_line["ordinal"]
        account = self.env["account.account"].browse(values.get("account_id")).exists()
        if not account:
            reject(self.env, "mapping_missing", ordinal=ordinal)
        account.check_access("read")
        if (
            company not in account.company_ids
            or account.deprecated
            or account.account_type
            in {"asset_receivable", "liability_payable", "off_balance"}
        ):
            reject(self.env, "account_invalid", ordinal=ordinal)
        tax = self.env["account.tax"].browse(values.get("tax_id")).exists()
        if not tax:
            reject(self.env, "tax_invalid", ordinal=ordinal)
        tax.check_access("read")
        rate, code = (0, 0) if document_line["exempt"] else (19, 14)
        if (
            tax.company_id != company
            or not tax.active
            or tax.type_tax_use != "purchase"
            or tax.amount_type != "percent"
            or decimal(tax.amount) != rate
            or tax.l10n_cl_sii_code != code
            or tax.price_include
            or tax.include_base_amount
            or tax.children_tax_ids
        ):
            reject(self.env, "tax_invalid", ordinal=ordinal)
        if document_line["exempt"] and tax != company.dte_inbox_account_exempt_tax_id:
            reject(self.env, "tax_invalid", ordinal=ordinal)
        product = self.env["product.product"].browse(values.get("product_id")).exists()
        if values.get("product_id") and (
            not product
            or not product.active
            or product.company_id not in (company, self.env["res.company"])
        ):
            reject(self.env, "account_invalid", ordinal=ordinal)
        if product:
            product.check_access("read")
        unit = self.env["uom.uom"].browse(values.get("uom_id")).exists()
        if unit:
            unit.check_access("read")
        if (
            (values.get("uom_id") and not unit)
            or (document_line["unit"] and not unit)
            or (
                unit
                and (
                    not unit.active
                    or (product and unit.category_id != product.uom_id.category_id)
                )
            )
        ):
            reject(self.env, "uom_unresolved", ordinal=ordinal)
