/** @odoo-module **/

import {PaymentScreen} from "@point_of_sale/app/screens/payment_screen/payment_screen";
import {patch} from "@web/core/utils/patch";

patch(PaymentScreen.prototype, {
    setup() {
        super.setup(...arguments);
        const mode = this.pos.session.dte_pos_accounting_mode;
        if (mode) {
            this.currentOrder.set_to_invoice(mode === "per_order_invoice");
        }
    },
});
