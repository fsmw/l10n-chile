# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from decimal import Decimal

from odoo.exceptions import ValidationError


class NoteAdjustmentService:
    """Canonical arithmetic shared by accounting and direct POS credit notes."""

    def __init__(self, env):
        self.env = env

    def allocate_vat(self, vat_total, lines):
        """Distribute integer CLP tax by largest remainder and ordinal tie-break."""
        taxable = [line for line in lines if not line["exempt"]]
        net = sum(Decimal(line["subtotal"]) for line in taxable)
        result = {line["origin_line_number"]: 0 for line in lines}
        if not net or not vat_total:
            return result
        exact_values = []
        assigned = 0
        for line in taxable:
            exact = Decimal(vat_total) * Decimal(line["subtotal"]) / net
            floor = int(exact.to_integral_value(rounding="ROUND_FLOOR"))
            result[line["origin_line_number"]] = floor
            assigned += floor
            exact_values.append((exact - floor, line["origin_line_number"]))
        for _remainder, ordinal in sorted(
            exact_values, key=lambda item: (-item[0], item[1])
        )[: vat_total - assigned]:
            result[ordinal] += 1
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
        totals = snapshot["totals"]
        return {
            key: int(Decimal(str(totals.get(key) or 0)))
            for key in ("net", "exempt", "vat", "total")
        }

    def format_totals(self, values):
        result = {
            key: str(int(values[key])) if values[key] else None
            for key in ("net", "exempt", "vat", "total")
        }
        result["vat_rate"] = "19" if values["net"] else None
        return result

    def integer(self, value, label):
        amount = Decimal(str(value))
        if amount != amount.to_integral_value():
            raise ValidationError(
                self.env._("CLP %(label)s must be an integer.") % {"label": label}
            )
        return int(amount)
