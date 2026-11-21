# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models
from odoo.tools import LazyTranslate

_lt = LazyTranslate(__name__)

DOCUMENT_STEP_XMLID = "l10n_cl_dte_website_sale.dte_document"
DOCUMENT_STEP_HREF = "/shop/dte_document"


class Website(models.Model):
    _inherit = "website"

    def _l10n_cl_dte_checkout_enabled(self):
        """The boleta/factura step only exists for Chilean websites."""
        self.ensure_one()
        return self.company_id.account_fiscal_country_id.code == "CL"

    def _get_checkout_step_list(self):
        """Insert the tax document step right after the delivery step."""
        steps = super()._get_checkout_step_list()
        if not self._l10n_cl_dte_checkout_enabled():
            return steps
        index = next(
            (i for i, step in enumerate(steps) if "website_sale.checkout" in step[0]),
            None,
        )
        if index is None:
            return steps
        delivery_xmlids, delivery = steps[index]
        document_step = (
            [DOCUMENT_STEP_XMLID],
            {
                "name": _lt("Tax Document"),
                "current_href": DOCUMENT_STEP_HREF,
                "main_button": _lt("Continue checkout"),
                "main_button_href": delivery["main_button_href"],
                "back_button": _lt("Back to delivery"),
                "back_button_href": "/shop/checkout",
            },
        )
        following = [
            (
                xmlids,
                dict(
                    values,
                    back_button=_lt("Back to tax document"),
                    back_button_href=DOCUMENT_STEP_HREF,
                )
                if position == 0
                else values,
            )
            for position, (xmlids, values) in enumerate(steps[index + 1 :])
        ]
        return [
            *steps[:index],
            (delivery_xmlids, dict(delivery, main_button_href=DOCUMENT_STEP_HREF)),
            document_step,
            *following,
        ]
