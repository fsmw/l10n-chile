/** @odoo-module **/
/* Copyright 2026 FSMW - AGPL-3.0 or later. */
import {Component} from "@odoo/owl";
import {OrderReceipt} from "@point_of_sale/app/screens/receipt_screen/receipt/order_receipt";
import {patch} from "@web/core/utils/patch";

export class DteReturnStatus extends Component {
    static template = "l10n_cl_dte_pos_return.DteReturnStatus";
    static props = {status: Object};
}

patch(OrderReceipt, {components: {...OrderReceipt.components, DteReturnStatus}});
