/** @odoo-module **/

import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { onMounted } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";

patch(ProductScreen.prototype, {
    setup() {
        super.setup(...arguments);
        onMounted(() => {
            const order = this.pos.get_order();
            if (this.pos.isChileanCompany() && !order.get_partner()) {
                order.update({
                    partner_id: this.pos.session._consumidor_final_anonimo_id,
                    invoice_type: "boleta",
                    to_invoice: true,
                });
            }
        });
    },
});
