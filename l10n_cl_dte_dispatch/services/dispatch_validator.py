# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, models


class DteDispatchValidator(models.AbstractModel):
    _name = "l10n_cl.dte.dispatch.validator"
    _description = "DTE 52 local validator"

    @api.model
    def validate(self, dispatch):
        """Return the list of local errors of a dispatch; an empty list is valid.

        These are local rules only. XSD, TED and signature checks belong to the
        composition step, not to this validator.
        """
        errors = []
        if not dispatch.line_ids:
            errors.append(_("Add at least one line."))
        for line in dispatch.line_ids:
            errors.extend(self._validate_line(line))
        if (
            dispatch.departure_at
            and dispatch.arrival_at
            and dispatch.arrival_at < dispatch.departure_at
        ):
            errors.append(_("The arrival cannot be before the departure."))
        return errors

    @api.model
    def _validate_line(self, line):
        errors = []
        if line.quantity <= 0:
            errors.append(
                _("Line %s: the quantity must be positive.", line.description)
            )
        if line.unit_price < 0:
            errors.append(
                _("Line %s: the unit price cannot be negative.", line.description)
            )
        if line.tax_category == "taxable" and line.unit_price <= 0:
            errors.append(
                _("Line %s: a taxable line needs a unit price.", line.description)
            )
        return errors
