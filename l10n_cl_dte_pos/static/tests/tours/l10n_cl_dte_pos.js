/** @odoo-module **/

import * as Chrome from "@point_of_sale/../tests/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/tours/utils/dialog_util";
import * as ProductScreen from "@point_of_sale/../tests/tours/utils/product_screen_util";
import * as PaymentScreen from "@point_of_sale/../tests/tours/utils/payment_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/tours/utils/receipt_screen_util";
import * as TicketScreen from "@point_of_sale/../tests/tours/utils/ticket_screen_util";
import * as Offline from "@point_of_sale/../tests/tours/utils/offline_util";
import * as TextInputPopup from "@point_of_sale/../tests/tours/utils/text_input_popup_util";
import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("l10n_cl_pos_sale_refund_tour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            ProductScreen.addOrderline("POS DTE Tour Product", "2"),
            ProductScreen.clickPayButton(),
            {
                content: "default Chilean POS document is boleta",
                trigger: ".payment-screen .payment-buttons .js_invoice:contains('Boleta')",
            },
            PaymentScreen.clickInvoiceButton(),
            {
                content: "cashier can select factura",
                trigger: ".payment-screen .payment-buttons .js_invoice:contains('Factura')",
            },
            PaymentScreen.clickInvoiceButton(),
            {
                content: "cashier can return to boleta",
                trigger: ".payment-screen .payment-buttons .js_invoice:contains('Boleta')",
            },
            Offline.setOfflineMode(),
            PaymentScreen.clickPaymentMethod("POS DTE Cash", true, {
                remaining: "0",
            }),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            {
                content: "offline receipt is explicitly pending synchronization",
                trigger: ".receipt-screen:contains('DTE pendiente de sincronización')",
            },
            Offline.setOnlineMode(),
            Dialog.confirm("Continue with limited functionality"),
            {
                content: "notify POS that the connection has returned",
                trigger: "body",
                run: () => window.dispatchEvent(new Event("online")),
            },
            {
                content: "server folio is visible after reconnection sync",
                trigger: ".receipt-screen .pos-receipt-order-data:contains('N.º:')",
            },
            {
                content: "receipt shows a human-readable SII status",
                trigger: ".receipt-screen:contains('Estado SII: Pendiente de envío')",
            },
            ReceiptScreen.clickNextOrder(),
            ProductScreen.addOrderline("POS DTE Tour Product", "1"),
            ProductScreen.clickPartnerButton(),
            ProductScreen.searchCustomerValue("POS DTE Invoice Buyer"),
            ProductScreen.clickCustomer("POS DTE Invoice Buyer"),
            ProductScreen.clickPayButton(),
            {
                content: "identified customer starts with boleta selection",
                trigger: ".payment-screen .payment-buttons .js_invoice:contains('Boleta')",
            },
            PaymentScreen.clickInvoiceButton(),
            {
                content: "invoice choice is retained before the offline payment",
                trigger: ".payment-screen .payment-buttons .js_invoice:contains('Factura')",
            },
            Offline.setOfflineMode(),
            PaymentScreen.clickPaymentMethod("POS DTE Cash", true, {
                remaining: "0",
            }),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            {
                content: "offline invoice receipt has no fiscal folio or TED",
                trigger: ".receipt-screen:contains('DTE pendiente de sincronización')",
                run() {
                    const receipt = document.querySelector(".pos-receipt").innerText;
                    if (receipt.includes("N.º:") || receipt.includes("Timbre Electrónico SII")) {
                        throw new Error("offline invoice receipt showed fiscal evidence before sync");
                    }
                },
            },
            Offline.setOnlineMode(),
            Dialog.confirm("Continue with limited functionality"),
            {
                content: "notify POS when invoice network access returns",
                trigger: "body",
                run: () => window.dispatchEvent(new Event("online")),
            },
            {
                content: "invoice receives its fiscal folio after synchronization",
                trigger: ".receipt-screen .pos-receipt-order-data:contains('N.º:')",
            },
            {
                content: "invoice receipt shows its synchronized SII status",
                trigger: ".receipt-screen:contains('Estado SII: Pendiente de envío')",
            },
            ReceiptScreen.clickNextOrder(),
            ProductScreen.clickRefund(),
            TicketScreen.selectOrder("-0001"),
            ProductScreen.clickNumpad("1"),
            TicketScreen.toRefundTextContains("To Refund: 1.00"),
            TicketScreen.confirmRefund(),
            ProductScreen.isShown(),
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("POS DTE Cash", true, {
                remaining: "0",
            }),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            ReceiptScreen.clickNextOrder(),
            ProductScreen.clickRefund(),
            TicketScreen.selectOrder("-0001"),
            ProductScreen.clickNumpad("1"),
            TicketScreen.toRefundTextContains("To Refund: 1.00"),
            TicketScreen.confirmRefund(),
            ProductScreen.isShown(),
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("POS DTE Cash", true, {
                remaining: "0",
            }),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            ReceiptScreen.clickNextOrder(),
            ProductScreen.addOrderline("POS DTE Tour Product", "1"),
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("POS DTE Bank Transfer", true, {
                remaining: "0",
            }),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            ReceiptScreen.clickNextOrder(),
            ProductScreen.addOrderline("POS DTE Tour Product", "1"),
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("POS DTE Card", true, {
                remaining: "0",
            }),
            PaymentScreen.clickValidate(),
            TextInputPopup.inputText("CARD-TOUR-123"),
            Dialog.confirm(),
            ReceiptScreen.isShown(),
            {
                content: "card voucher appears on the Chilean POS receipt",
                trigger: ".receipt-screen:contains('Voucher: CARD-TOUR-123')",
            },
        ].flat(),
});
