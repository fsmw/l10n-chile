/** @odoo-module **/

import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";

const DTE_STATUS_LABELS = {
    not_sent: _t("Pendiente de envío"),
    ask_for_status: _t("En consulta"),
    accepted: _t("Aceptado"),
    objected: _t("Aceptado con reparos"),
    rejected: _t("Rechazado"),
    cancelled: _t("Cancelado"),
    error: _t("Error de comunicación"),
    manual: _t("Registrado manualmente"),
};

patch(PosOrder.prototype, {
    setup(vals) {
        super.setup(...arguments);
        if (this.isChileanCompany()) {
            this.to_invoice = true;
            this.invoice_type = vals.invoice_type || "boleta";
            this.voucher_number = vals.voucher_number || "";
        }
    },

    isChileanCompany() {
        return this.company?.country_id?.code === "CL";
    },

    isFactura() {
        return this.isChileanCompany() && this.invoice_type === "factura";
    },

    setInvoiceType(invoiceType) {
        this.assert_editable();
        this.update({ invoice_type: invoiceType });
    },

    set_to_invoice(to_invoice) {
        if (this.isChileanCompany()) {
            this.assert_editable();
            this.to_invoice = true;
            return;
        }
        super.set_to_invoice(...arguments);
    },

    is_to_invoice() {
        return this.isChileanCompany() || super.is_to_invoice(...arguments);
    },

    doNotAllowRefundAndSales() {
        return this.isChileanCompany() || super.doNotAllowRefundAndSales(...arguments);
    },

    serialize() {
        const data = super.serialize(...arguments);
        if (this.isChileanCompany()) {
            data.to_invoice = true;
            data.invoice_type = this.invoice_type || "boleta";
            data.voucher_number = this.voucher_number || "";
        }
        return data;
    },

    export_for_printing(baseUrl, headerData) {
        const result = super.export_for_printing(...arguments);
        if (!this.isChileanCompany()) {
            return result;
        }
        return {
            ...result,
            voucher_number: this.voucher_number,
            l10n_cl_dte_ted_image: this.l10n_cl_dte_pos_ted_image,
            l10n_cl_dte_status: this.l10n_cl_dte_status,
            l10n_cl_dte_status_label:
                DTE_STATUS_LABELS[this.l10n_cl_dte_status] ||
                _t("Estado no reconocido"),
            l10n_cl_dte_resolution_number:
                this.company.l10n_cl_dte_resolution_number,
            l10n_cl_dte_resolution_date: this.company.l10n_cl_dte_resolution_date,
        };
    },
});
