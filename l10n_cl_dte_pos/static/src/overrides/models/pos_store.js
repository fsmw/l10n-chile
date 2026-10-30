/** @odoo-module **/

import { PosStore } from "@point_of_sale/app/store/pos_store";
import { patch } from "@web/core/utils/patch";
import { pick } from "@web/core/utils/objects";
import { formatDateTime } from "@web/core/l10n/dates";
import { parseUTCString } from "@point_of_sale/utils";

patch(PosStore.prototype, {
    isChileanCompany() {
        return this.company?.country_id?.code === "CL";
    },

    createNewOrder() {
        const order = super.createNewOrder(...arguments);
        if (this.isChileanCompany() && !order.get_partner()) {
            order.update({
                partner_id: this.session._consumidor_final_anonimo_id,
                invoice_type: "boleta",
                to_invoice: true,
            });
        }
        return order;
    },

    getSyncAllOrdersContext(orders, options = {}) {
        const context = super.getSyncAllOrdersContext(...arguments);
        if (this.isChileanCompany()) {
            // POS prints the Chilean tax receipt; avoid generating and mailing a second PDF.
            return { ...context, generate_pdf: false };
        }
        return context;
    },

    getReceiptHeaderData(order) {
        const result = super.getReceiptHeaderData(...arguments);
        if (!this.isChileanCompany() || !order) {
            return result;
        }
        result.company.cl_vat = this.session._l10n_cl_company_vat;
        result.l10n_latam_document_type =
            order.l10n_cl_dte_document_type_name;
        result.l10n_latam_document_number =
            order.l10n_cl_dte_document_number;
        result.date = formatDateTime(parseUTCString(order.date_order));
        const partner = order.get_partner();
        result.partner = order.isFactura() && partner
            ? pick(partner, "name", "vat", "street", "street2", "city", "l10n_cl_activity_description", "phone")
            : false;
        return result;
    },
});
