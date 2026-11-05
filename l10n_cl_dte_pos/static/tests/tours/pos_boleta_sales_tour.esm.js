/** @odoo-module **/
/* global document */

import * as Chrome from "@point_of_sale/../tests/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/tours/utils/dialog_util";
import * as PartnerList from "@point_of_sale/../tests/tours/utils/partner_list_util";
import * as PaymentScreen from "@point_of_sale/../tests/tours/utils/payment_screen_util";
import * as ProductScreen from "@point_of_sale/../tests/tours/utils/product_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/tours/utils/receipt_screen_util";
import * as TicketScreen from "@point_of_sale/../tests/tours/utils/ticket_screen_util";
import {registry} from "@web/core/registry";

function sale(split) {
    return [
        ProductScreen.clickDisplayedProduct("DTE Browser Product"),
        ProductScreen.clickPartnerButton(),
        PartnerList.clickPartner("Synthetic POS Receiver"),
        ProductScreen.clickPayButton(),
        PaymentScreen.isShown(),
        {
            trigger: ".payment-screen",
            run() {
                if (document.querySelector(".js_invoice")) {
                    throw new Error("The cashier can still change the company policy.");
                }
            },
        },
        PaymentScreen.clickPaymentMethod("Cash"),
        ...(split
            ? [
                  PaymentScreen.clickNumpad("5 0"),
                  PaymentScreen.clickPaymentMethod("Bank"),
              ]
            : []),
        PaymentScreen.clickValidate(),
        ReceiptScreen.isShown(),
        ReceiptScreen.clickNextOrder(),
    ].flat();
}

registry.category("web_tour.tours").add("l10n_cl_dte_pos_sales", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            ...sale(false),
            ...sale(true),
        ].flat(),
});

registry.category("web_tour.tours").add("l10n_cl_dte_pos_close", {
    steps: () =>
        [
            Chrome.startPoS(),
            Chrome.clickMenuOption("Close Register"),
            {trigger: ".close-pos-popup .fa-clone", run: "click"},
            {...Dialog.confirm("Close Register"), expectUnloadPage: true},
            {trigger: ".screen-login .open-register-btn"},
        ].flat(),
});

registry.category("web_tour.tours").add("l10n_cl_dte_pos_refund_blocked", {
    steps: () =>
        [
            Chrome.startPoS(),
            ProductScreen.clickRefund(),
            TicketScreen.selectOrder("-0001"),
            ProductScreen.clickNumpad("1"),
            TicketScreen.confirmRefund(),
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("Cash"),
            PaymentScreen.clickValidate(),
            Dialog.bodyIs("referenced credit note"),
            Dialog.confirm(),
        ].flat(),
});

registry.category("web_tour.tours").add("l10n_cl_dte_pos_foreign_policy", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            ProductScreen.clickDisplayedProduct("DTE Browser Product"),
            ProductScreen.clickPayButton(),
            {trigger: ".payment-screen .js_invoice"},
        ].flat(),
});
