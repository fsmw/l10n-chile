/** @odoo-module **/
/* Copyright 2026 FSMW - AGPL-3.0 or later. */
import * as Chrome from "@point_of_sale/../tests/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/tours/utils/dialog_util";
import * as PaymentScreen from "@point_of_sale/../tests/tours/utils/payment_screen_util";
import * as ProductScreen from "@point_of_sale/../tests/tours/utils/product_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/tours/utils/receipt_screen_util";
import * as TicketScreen from "@point_of_sale/../tests/tours/utils/ticket_screen_util";
import * as PartnerList from "@point_of_sale/../tests/tours/utils/partner_list_util";
import {registry} from "@web/core/registry";

registry.category("web_tour.tours").add("l10n_cl_dte_pos_return_original_sale", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            ProductScreen.clickDisplayedProduct("DTE Browser Product"),
            ProductScreen.clickNumpad("2"),
            ProductScreen.clickPartnerButton(),
            PartnerList.clickPartner("Synthetic POS Receiver"),
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("Cash"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            ReceiptScreen.clickNextOrder(),
        ].flat(),
});

registry.category("web_tour.tours").add("l10n_cl_dte_pos_return_cross_store", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            ProductScreen.clickRefund(),
            TicketScreen.selectOrder("-0001"),
            ProductScreen.clickNumpad("1"),
            TicketScreen.confirmRefund(),
            {trigger: ".modal-dialog textarea", run: "edit Goods return from store B"},
            Dialog.confirm(),
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("Store B cash"),
            {
                trigger: ".payment-screen",
                run() {
                    const env = odoo.__WOWL_DEBUG__.root.env;
                    const pos = env.services.pos;
                    const call = pos.data.call;
                    const hardware = env.services.hardware_proxy;
                    const openCashbox = hardware.openCashbox;
                    pos.dteReturnTest = {uuid: pos.get_order().uuid, cashboxCount: 0};
                    hardware.openCashbox = function (...args) {
                        pos.dteReturnTest.cashboxCount += 1;
                        return openCashbox.apply(this, args);
                    };
                    pos.data.call = async function (...args) {
                        const result = await call.apply(this, args);
                        if (
                            args[0] === "pos.order" &&
                            args[1] === "get_dte_return_receipt"
                        ) {
                            pos.data.call = call;
                            throw new Error(
                                "Synthetic loss after the committed server response"
                            );
                        }
                        return result;
                    };
                },
            },
            PaymentScreen.clickValidate(),
            Dialog.bodyIs("Do not hand over another refund"),
            {
                trigger: ".modal-dialog",
                run() {
                    const pos = odoo.__WOWL_DEBUG__.root.env.services.pos;
                    if (
                        pos.dteReturnTest.cashboxCount !== 0 ||
                        pos.get_order().uuid !== pos.dteReturnTest.uuid
                    ) {
                        throw new Error(
                            "An uncertain return opened the cashbox or changed identity"
                        );
                    }
                },
            },
            Dialog.confirm(),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            {
                trigger:
                    ".o_l10n_cl_dte_pos_return_status:contains('Electronic credit note 61')",
            },
        ].flat(),
});
