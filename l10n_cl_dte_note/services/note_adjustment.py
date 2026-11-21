# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from decimal import Decimal

from odoo.exceptions import ValidationError


class NoteAdjustmentService:
    """Canonical arithmetic shared by accounting and direct POS credit notes."""

    def __init__(self, env):
        self.env = env

    @staticmethod
    def allocate(total, weights):
        """Split an integer CLP amount by largest remainder, ordinal tie-break.

        ``weights`` maps an ordinal to its non-negative base; ordinals with a
        zero base receive nothing.
        """
        total = int(total)
        result = {ordinal: 0 for ordinal in weights}
        base = sum((Decimal(str(weight)) for weight in weights.values()), Decimal(0))
        if not base or not total:
            return result
        exact_values = []
        assigned = 0
        for ordinal, weight in weights.items():
            exact = Decimal(total) * Decimal(str(weight)) / base
            floor = int(exact.to_integral_value(rounding="ROUND_FLOOR"))
            result[ordinal] = floor
            assigned += floor
            exact_values.append((exact - floor, ordinal))
        for _remainder, ordinal in sorted(
            exact_values, key=lambda item: (-item[0], item[1])
        )[: total - assigned]:
            result[ordinal] += 1
        return result

    def allocate_vat(self, vat_total, lines, weight_key="subtotal"):
        """Distribute integer CLP tax by largest remainder and ordinal tie-break."""
        result = {line["origin_line_number"]: 0 for line in lines}
        if not vat_total:
            return result
        result.update(
            self.allocate(
                vat_total,
                {
                    line["origin_line_number"]: Decimal(str(line[weight_key]))
                    for line in lines
                    if not line["exempt"]
                },
            )
        )
        return result

    def summary_lines(self, details):
        groups = []
        for exempt in (False, True):
            selected = [line for line in details if line["exempt"] == exempt]
            if not selected:
                continue
            total = sum(int(line["subtotal"]) for line in selected)
            groups.append(
                {
                    "origin_line_numbers": [
                        line["origin_line_number"] for line in selected
                    ],
                    "name": self.env._("Exempt reversal")
                    if exempt
                    else self.env._("Taxable reversal"),
                    "description": self.env._(
                        "Full cancellation of referenced document"
                    ),
                    "quantity": "1",
                    "uom": "UN",
                    "unit_price": str(total),
                    "discount_percent": None,
                    "subtotal": str(total),
                    "exempt": exempt,
                }
            )
        return groups

    def snapshot_totals(self, snapshot):
        """Return the comparable integer totals, with additional SII taxes."""
        totals = snapshot["totals"]
        result = {
            key: int(Decimal(str(totals.get(key) or 0)))
            for key in ("net", "exempt", "vat", "total")
        }
        additional = self.additional_taxes(totals)
        if additional:
            result["additional"] = sum(item["amount"] for item in additional)
            result["additional_taxes"] = additional
        return result

    @staticmethod
    def additional_taxes(totals):
        """Normalize ``totals.additional_taxes`` to sorted integer entries."""
        return sorted(
            (
                {
                    "code": str(item["code"]),
                    "rate": format(Decimal(str(item["rate"])).normalize(), "f"),
                    "amount": int(Decimal(str(item["amount"]))),
                }
                for item in totals.get("additional_taxes") or []
            ),
            key=lambda item: int(item["code"]),
        )

    def format_totals(self, values):
        result = {
            key: str(int(values[key])) if values[key] else None
            for key in ("net", "exempt", "vat", "total")
        }
        result["vat_rate"] = "19" if values["net"] else None
        if values.get("additional_taxes"):
            result["additional_taxes"] = [
                {**item, "amount": str(int(item["amount"]))}
                for item in values["additional_taxes"]
            ]
        return result

    def integer(self, value, label):
        amount = Decimal(str(value))
        if amount != amount.to_integral_value():
            raise ValidationError(
                self.env._("CLP %(label)s must be an integer.") % {"label": label}
            )
        return int(amount)
