/** @odoo-module **/

import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { TextInputPopup } from "@point_of_sale/app/utils/input_popups/text_input_popup";
import { makeAwaitable } from "@point_of_sale/app/store/make_awaitable_dialog";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";

patch(PaymentScreen.prototype, {
    toggleIsToInvoice() {
        if (this.pos.isChileanCompany()) {
            const order = this.currentOrder;
            order.setInvoiceType(order.invoice_type === "factura" ? "boleta" : "factura");
            this.render(true);
            return;
        }
        super.toggleIsToInvoice(...arguments);
    },

    highlightInvoiceButton() {
        return this.pos.isChileanCompany()
            ? this.currentOrder.isFactura()
            : this.currentOrder.is_to_invoice();
    },

    async _isOrderValid(isForceValidate) {
        const result = await super._isOrderValid(...arguments);
        if (!this.pos.isChileanCompany() || !result) {
            return result;
        }

        const order = this.currentOrder;
        const partner = order.get_partner();
        if (order.isFactura()) {
            const requiredFields = [
                "l10n_cl_dte_email",
                "l10n_cl_activity_description",
                "street",
                "l10n_latam_identification_type_id",
                "l10n_cl_sii_taxpayer_type",
                "vat",
            ];
            const missingFields = requiredFields.filter((field) => !partner?.[field]);
            if (!partner || partner.id === this.pos.session._consumidor_final_anonimo_id) {
                missingFields.push("cliente con RUT");
            }
            if (missingFields.length) {
                this.notification.add(
                    _t("Complete los datos requeridos del cliente:") +
                        " " +
                        missingFields.join(", ")
                );
                if (partner && partner.id !== this.pos.session._consumidor_final_anonimo_id) {
                    this.pos.editPartner(partner);
                } else {
                    this.pos.selectPartner();
                }
                return false;
            }
        }
        return true;
    },

    async validateOrder(isForceValidate) {
        if (
            this.pos.isChileanCompany() &&
            this.paymentLines.some((line) => line.payment_method_id.is_card_payment)
        ) {
            const voucherNumber = await makeAwaitable(this.dialog, TextInputPopup, {
                rows: 1,
                title: _t("Número de voucher de tarjeta"),
            });
            if (!voucherNumber?.trim()) {
                return;
            }
            this.currentOrder.update({ voucher_number: voucherNumber.trim() });
        }
        return super.validateOrder(...arguments);
    },

    shouldDownloadInvoice() {
        return this.pos.isChileanCompany()
            ? false
            : super.shouldDownloadInvoice(...arguments);
    },
});
