# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""SII additional taxes and global discounts carried by DTE notes.

Global discount allocation rule
-------------------------------
An original DTE lists its global discounts (``DscRcgGlobal``) once for the
whole document. Each original detail line receives a share of them:

* the taxable discounts are split among the taxable lines and the exempt
  discounts among the exempt lines, proportionally to the line amount
  (``MontoItem``), in integer CLP by largest remainder with the line ordinal
  as tie-break. The line capacity is then the line amount minus its share.

A partial credit note crediting ``x`` CLP of a line amount takes, from the
line's *remaining* share ``Rd`` of the *remaining* line amount ``Rg``,
``Rd`` when ``x == Rg`` and ``round_half_up(Rd * x / Rg)`` otherwise. The
last credit of a line therefore consumes its share exactly, every credit is
bounded by what remains, and the credited net (``x`` minus its share) never
exceeds the remaining net of the line. A full cancellation reproduces the
referenced document's discounts verbatim.

Additional taxes (``ImptoReten``) are posted on the line amount, because a
global discount cannot carry them; their capacity is split among the lines
with the same SII code proportionally to the line amount, like VAT is split
by the line net.
"""

from decimal import ROUND_HALF_UP, Decimal

from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_xml.services.invoice_snapshot import InvoiceSnapshotService

from .note_adjustment import NoteAdjustmentService


def decimal_text(value):
    return format(Decimal(str(value)).normalize(), "f")


class NoteFiscalComponents:
    """Allocate additional taxes and global discounts of original DTEs."""

    ADDITIONAL_TAX_CODES = InvoiceSnapshotService.ADDITIONAL_TAX_CODES

    def __init__(self, env):
        self.env = env
        self.math = NoteAdjustmentService(env)

    # Original documents -------------------------------------------------

    def original_lines(self, snapshot):
        """Return the fiscal components of every original detail line."""
        result = {}
        for ordinal, line in enumerate(snapshot["lines"], start=1):
            result[ordinal] = {
                "gross": Decimal(str(line["subtotal"])),
                "is_exempt": bool(line.get("exempt")),
                "code": line.get("additional_tax_code") or None,
                "quantity": Decimal(str(line.get("quantity") or 0)),
            }
        shares = self.discount_shares(snapshot, result)
        for ordinal, line in result.items():
            line["discount"] = Decimal(shares[ordinal])
            line["base"] = line["gross"] - line["discount"]
        totals = snapshot["totals"]
        vat = self.math.allocate(
            Decimal(str(totals.get("vat") or 0)),
            {
                ordinal: line["base"]
                for ordinal, line in result.items()
                if not line["is_exempt"] and line["base"]
            },
        )
        additional = self.allocate_additional(
            self.math.additional_taxes(totals),
            {
                ordinal: (line["code"], line["gross"])
                for ordinal, line in result.items()
            },
        )
        return {
            ordinal: {
                "net": Decimal(0) if line["is_exempt"] else line["base"],
                "exempt": line["base"] if line["is_exempt"] else Decimal(0),
                "vat": Decimal(vat.get(ordinal, 0)),
                "additional": Decimal(additional[ordinal]),
                "gross": line["gross"],
                "discount": line["discount"],
                "total": line["base"]
                + Decimal(vat.get(ordinal, 0))
                + Decimal(additional[ordinal]),
                "quantity": line["quantity"],
            }
            for ordinal, line in result.items()
        }

    def discount_shares(self, snapshot, lines):
        """Split taxable and exempt global discounts among their lines."""
        shares = {ordinal: 0 for ordinal in lines}
        for exempt in (False, True):
            total = sum(
                (
                    Decimal(str(item["value"]))
                    for item in snapshot.get("global_discounts") or []
                    if bool(item.get("exempt")) == exempt
                ),
                Decimal(0),
            )
            if not total:
                continue
            shares.update(
                self.math.allocate(
                    total,
                    {
                        ordinal: line["gross"]
                        for ordinal, line in lines.items()
                        if line["is_exempt"] == exempt and line["gross"]
                    },
                )
            )
        return shares

    def allocate_additional(self, additional_taxes, lines):
        """Split each additional tax among the lines with its SII code.

        ``lines`` maps an ordinal to ``(code, base)``.
        """
        result = {ordinal: 0 for ordinal in lines}
        for item in additional_taxes:
            weights = {
                ordinal: base
                for ordinal, (code, base) in lines.items()
                if code == item["code"] and base
            }
            if not weights:
                raise ValidationError(
                    self.env._(
                        "The additional SII tax %(code)s has no detail line "
                        "that carries it."
                    )
                    % {"code": item["code"]}
                )
            result.update(self.math.allocate(item["amount"], weights))
        return result

    def target_line_discounts(self, snapshot):
        """Return the global discount share of each referenced detail line."""
        effects = snapshot.get("adjustment", {}).get("line_effects")
        if effects:
            return {
                int(effect["origin_line_number"]): int(effect.get("discount") or 0)
                for effect in effects
            }
        return {
            ordinal: int(values["discount"])
            for ordinal, values in self.original_lines(snapshot).items()
        }

    @staticmethod
    def partial_discount(amount, remaining_amount, remaining_discount):
        """Share of the remaining line discount taken by a partial credit."""
        amount, remaining_amount = Decimal(amount), Decimal(remaining_amount)
        if amount >= remaining_amount:
            return int(remaining_discount)
        if not remaining_discount:
            return 0
        return int(
            (Decimal(remaining_discount) * amount / remaining_amount).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )

    # Note accounting ----------------------------------------------------

    def source_rates(self, snapshot):
        return {
            item["code"]: item["rate"]
            for item in self.math.additional_taxes(snapshot["totals"])
        }

    def line_additional_code(self, line, original, rates):
        """Validate the note line taxes and return its SII additional code."""
        additional = line.tax_ids.filtered(
            lambda tax: tax.l10n_cl_sii_code in self.ADDITIONAL_TAX_CODES
        )
        if any(
            tax.amount and (tax.amount_type != "percent" or abs(tax.amount) != 19)
            for tax in line.tax_ids - additional
        ):
            raise ValidationError(
                self.env._("DTE notes support only 19% VAT and exempt lines.")
            )
        if len(additional) > 1:
            raise ValidationError(
                self.env._("A DTE note line supports one additional SII tax.")
            )
        code = str(additional.l10n_cl_sii_code) if additional else None
        if code != (
            str(original["additional_tax_code"])
            if original.get("additional_tax_code")
            else None
        ):
            raise ValidationError(
                self.env._(
                    "A note line must carry the same additional SII tax as its "
                    "original DTE line."
                )
            )
        if additional and (
            additional.amount_type != "percent"
            or decimal_text(abs(additional.amount)) != rates.get(code)
        ):
            raise ValidationError(
                self.env._(
                    "The additional SII tax rate of a note must match the "
                    "referenced DTE."
                )
            )
        return code

    def move_additional_taxes(self, move, rates):
        """Group the note's posted additional taxes by SII code."""
        sign = -1 if move.move_type == "out_invoice" else 1
        amounts = {}
        for line in move.line_ids.filtered(
            lambda item: item.tax_line_id.l10n_cl_sii_code in self.ADDITIONAL_TAX_CODES
        ):
            code = str(line.tax_line_id.l10n_cl_sii_code)
            if decimal_text(abs(line.tax_line_id.amount)) != rates.get(code):
                raise ValidationError(
                    self.env._(
                        "The additional SII tax rate of a note must match the "
                        "referenced DTE."
                    )
                )
            amounts[code] = (
                amounts.get(code, Decimal(0)) + Decimal(str(line.balance)) * sign
            )
        result = []
        for code in sorted(amounts, key=int):
            amount = self.math.integer(amounts[code], self.env._("additional tax"))
            if amount <= 0:
                raise ValidationError(
                    self.env._("Note additional SII taxes must be positive.")
                )
            result.append({"code": code, "rate": rates[code], "amount": amount})
        return result

    def discount_lines(self, move, snapshot):
        """Return the accounting lines that carry the note's global discounts."""
        if not snapshot.get("global_discounts"):
            return move.env["account.move.line"]
        return move.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
            and line.price_subtotal < 0
            and not line.dte_note_origin_line_number
        )

    def discount_line_values(self, lines):
        """Validate global discount lines and return their DscRcgGlobal rows."""
        result = []
        for line in lines:
            if line.tax_ids.filtered(
                lambda tax: tax.l10n_cl_sii_code in self.ADDITIONAL_TAX_CODES
            ) or any(
                tax.amount and (tax.amount_type != "percent" or tax.amount != 19)
                for tax in line.tax_ids
            ):
                raise ValidationError(
                    self.env._(
                        "A note global discount supports only 19% VAT or no tax."
                    )
                )
            value = self.math.integer(-line.price_subtotal, self.env._("discount"))
            result.append(
                {
                    "description": (line.name or self.env._("Discount")).strip()[:45],
                    "value": str(value),
                    "exempt": not any(tax.amount for tax in line.tax_ids),
                }
            )
        return result
