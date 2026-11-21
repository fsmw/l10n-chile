# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.http import request, route

from odoo.addons.website_sale.controllers.main import WebsiteSale

from ..models.website import DOCUMENT_STEP_HREF, DOCUMENT_STEP_XMLID


class WebsiteSaleDteDocument(WebsiteSale):
    @route(
        DOCUMENT_STEP_HREF,
        type="http",
        methods=["GET", "POST"],
        auth="public",
        website=True,
        sitemap=False,
    )
    def shop_dte_document(self, **post):
        """Let the customer choose a boleta or a factura for the current cart.

        Only the current session cart is written, and only after the
        submitted values pass the server-side validation of the order.
        """
        order_sudo = request.website.sale_get_order()
        if redirection := self._check_cart_and_addresses(order_sudo):
            return redirection
        if not order_sudo._l10n_cl_dte_checkout_applies():
            return request.redirect("/shop/confirm_order")
        errors = {}
        if request.httprequest.method == "POST":
            values = order_sudo._l10n_cl_dte_parse_checkout_form(post)
            errors = order_sudo._l10n_cl_dte_checkout_errors(values)
            if not errors:
                order_sudo._l10n_cl_dte_set_checkout_choice(values)
                step = request.website._get_checkout_steps(DOCUMENT_STEP_XMLID)
                return request.redirect(step["main_button_href"])
        else:
            values = order_sudo._l10n_cl_dte_checkout_defaults()
        return request.render(
            DOCUMENT_STEP_XMLID,
            self._l10n_cl_dte_document_page_values(order_sudo, values, errors),
        )

    def _l10n_cl_dte_document_page_values(self, order_sudo, values, errors):
        billing_sudo = order_sudo.partner_invoice_id
        return {
            "order": order_sudo,
            "website_sale_order": order_sudo,
            "dte_values": values,
            "dte_errors": errors,
            "billing_partner": billing_sudo,
            "can_edit_billing": billing_sudo._can_be_edited_by_current_customer(
                order_sudo, "billing"
            ),
        }

    @route()
    def shop_confirm_order(self, **post):
        """Ask for the tax document when the delivery step was skipped."""
        order_sudo = request.website.sale_get_order()
        if (
            order_sudo
            and order_sudo.state == "draft"
            and order_sudo._l10n_cl_dte_checkout_applies()
            and not order_sudo.l10n_cl_dte_document_kind
        ):
            return request.redirect(DOCUMENT_STEP_HREF)
        return super().shop_confirm_order(**post)
