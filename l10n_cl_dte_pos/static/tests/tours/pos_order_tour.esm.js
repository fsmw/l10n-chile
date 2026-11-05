/** @odoo-module **/
/* global document */

import * as Chrome from "@point_of_sale/../tests/tours/utils/chrome_util";
import * as PaymentScreen from "@point_of_sale/../tests/tours/utils/payment_screen_util";
import * as ProductScreen from "@point_of_sale/../tests/tours/utils/product_screen_util";
import {registry} from "@web/core/registry";

registry.category("web_tour.tours").add("l10n_cl_dte_pos_payment_controls", {
    steps: () => [
        Chrome.startPoS(),
        ProductScreen.clickPayButton(),
        PaymentScreen.isShown(),
        {
            trigger: ".payment-screen",
            run: () => {
                if (document.querySelector(".js_invoice")) {
                    throw new Error("The cashier can still change POS invoicing.");
                }
            },
        },
    ],
});
