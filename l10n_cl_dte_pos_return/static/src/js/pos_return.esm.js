/** @odoo-module **/
/* global navigator */
/* Copyright 2026 FSMW - AGPL-3.0 or later. */
import {TicketScreen} from "@point_of_sale/app/screens/ticket_screen/ticket_screen";
import {PaymentScreen} from "@point_of_sale/app/screens/payment_screen/payment_screen";
import {PosOrder} from "@point_of_sale/app/models/pos_order";
import {PosOrderline} from "@point_of_sale/app/models/pos_order_line";
import {TextInputPopup} from "@point_of_sale/app/utils/input_popups/text_input_popup";
import {makeAwaitable} from "@point_of_sale/app/store/make_awaitable_dialog";
import {AlertDialog} from "@web/core/confirmation_dialog/confirmation_dialog";
import {serializeDateTime} from "@web/core/l10n/dates";
import {_t} from "@web/core/l10n/translation";
import {patch} from "@web/core/utils/patch";
import {RPCError} from "@web/core/network/rpc";

const isRefund = (order) => order.lines.some((line) => line.qty < 0);
const isEnabled = (pos) =>
    pos.company.country_id?.code === "CL" && pos.session.dte_pos_returns_enabled;

async function preview(pos, order) {
    if (!navigator.onLine) {
        throw new Error(_t("Connect before confirming a Chilean POS return."));
    }
    const originals = new Set(
        order.lines.map((line) => line.refunded_orderline_id?.order_id?.id)
    );
    if (
        originals.size !== 1 ||
        originals.has(undefined) ||
        order.lines.some((line) => line.qty >= 0)
    ) {
        throw new Error(
            _t("Return only products from one original sale in this order.")
        );
    }
    const result = await pos.data.call("pos.order", "get_dte_return_preview", [], {
        config_id: pos.config.id,
        original_order_id: [...originals][0],
        requested_lines: order.lines.map((line) => ({
            original_line_id: line.refunded_orderline_id.id,
            quantity: String(-line.qty),
        })),
        reason: order.dte_return_reason,
    });
    order.uiState.dteReturnPreview = result;
    order.set_to_invoice(result.accounting_mode === "per_order_invoice");
    return result;
}

patch(TicketScreen.prototype, {
    async print(order) {
        if (
            this.pos.company.country_id?.code === "CL" &&
            isRefund(order) &&
            order.raw.dte_return_target_document_id
        ) {
            order.uiState.dteReturnReceipt = await this.pos.data.call(
                "pos.order",
                "get_dte_return_receipt",
                [],
                {config_id: this.pos.config.id, order_uuid: order.uuid}
            );
        }
        return await super.print(...arguments);
    },
    async addAdditionalRefundInfo(order, destinationOrder) {
        await super.addAdditionalRefundInfo(...arguments);
        if (!isEnabled(this.pos)) {
            return;
        }
        const reason = await makeAwaitable(this.dialog, TextInputPopup, {
            title: _t("Credit note return reason"),
            startingValue: destinationOrder.dte_return_reason || "",
            placeholder: _t("Explain the goods return"),
        });
        if (!reason?.trim()) {
            throw new Error(_t("Enter a return reason before proceeding to payment."));
        }
        destinationOrder.dte_return_reason = reason.trim();
        await preview(this.pos, destinationOrder);
    },
    async onDoRefund() {
        if (!isEnabled(this.pos)) {
            return await super.onDoRefund(...arguments);
        }
        try {
            return await super.onDoRefund(...arguments);
        } catch (error) {
            this.dialog.add(AlertDialog, {
                title: _t("Return could not be prepared"),
                body: error.data?.message || error.message,
            });
        }
    },
    async _fetchSyncedOrders() {
        if (!isEnabled(this.pos)) {
            return await super._fetchSyncedOrders(...arguments);
        }
        const screenState = this.pos.ticketScreenState;
        const domain = this._computeSyncedOrdersDomain();
        const key = JSON.stringify(domain);
        const {ordersInfo, totalCount} = await this.pos.data.call(
            "pos.order",
            "search_dte_return_order_ids",
            [],
            {
                config_id: this.pos.config.id,
                search_term: this.state.search.searchTerm || "",
                limit: 30,
                offset: screenState.offsetByDomain[key] || 0,
            }
        );
        screenState.offsetByDomain[key] =
            (screenState.offsetByDomain[key] || 0) + ordersInfo.length;
        screenState.totalCount = totalCount;
        if (ordersInfo.length) {
            const records = await this.pos.data.callRelated(
                "pos.order",
                "get_ticket_screen_order_data",
                [ordersInfo.map((info) => info[0])]
            );
            await this.pos.processProductAttributesByProducts(
                records["product.product"]
            );
        }
    },
});

patch(PaymentScreen.prototype, {
    setup() {
        super.setup(...arguments);
        if (
            isEnabled(this.pos) &&
            isRefund(this.currentOrder) &&
            this.currentOrder.uiState.dteReturnPreview
        ) {
            this.currentOrder.set_to_invoice(
                this.currentOrder.uiState.dteReturnPreview.accounting_mode ===
                    "per_order_invoice"
            );
        }
    },
    async addNewPaymentLine(paymentMethod) {
        if (
            isEnabled(this.pos) &&
            isRefund(this.currentOrder) &&
            (paymentMethod.use_payment_terminal ||
                paymentMethod.type === "pay_later" ||
                paymentMethod.payment_method_type !== "none")
        ) {
            this.dialog.add(AlertDialog, {
                title: _t("Manual refund required"),
                body: _t(
                    "Use a manual cash or bank refund method for this credit note."
                ),
            });
            return false;
        }
        return await super.addNewPaymentLine(...arguments);
    },
    async validateOrder(isForceValidate) {
        if (isEnabled(this.pos) && isRefund(this.currentOrder)) {
            try {
                if (this.currentOrder.uiState.dteReturnUncertain) {
                    return await this._finalizeValidation();
                }
                await preview(this.pos, this.currentOrder);
            } catch (error) {
                this.dialog.add(AlertDialog, {
                    title: _t("Online return validation required"),
                    body: error.data?.message || error.message,
                });
                return;
            }
        }
        return await super.validateOrder(isForceValidate);
    },
    async _finalizeValidation() {
        if (!isEnabled(this.pos) || !isRefund(this.currentOrder)) {
            return await super._finalizeValidation(...arguments);
        }
        const order = this.currentOrder;
        let synchronized = false;
        this.env.services.ui.block();
        try {
            order.date_order = order.uiState.dteReturnUncertain
                ? order.date_order
                : serializeDateTime(luxon.DateTime.now());
            this.pos.addPendingOrder([order.id]);
            order.state = "paid";
            order.uiState.dteReturnUncertain = true;
            const result = await this.pos.syncAllOrders({orders: [order], throw: true});
            if (!result) {
                throw new Error(_t("The server has not confirmed this return."));
            }
            synchronized = true;
            const status = await this.pos.data.call(
                "pos.order",
                "get_dte_return_receipt",
                [],
                {config_id: this.pos.config.id, order_uuid: order.uuid}
            );
            if (["rejected", "outcome_unknown"].includes(status.status)) {
                throw new Error(
                    _t("This fiscal refund requires manager review before payout.")
                );
            }
            order.uiState.dteReturnReceipt = status;
            order.uiState.dteReturnUncertain = false;
            if (order.is_paid_with_cash()) {
                this.hardwareProxy.openCashbox();
            }
            await this.afterOrderValidation(true);
        } catch (error) {
            if (error instanceof RPCError && !synchronized) {
                order.state = "draft";
                order.uiState.dteReturnUncertain = false;
                this.pos.removePendingOrder(order);
            }
            // Keep the same UUID and prevent edits until the uncertain response is recovered.
            this.dialog.add(AlertDialog, {
                title: _t("Refund not confirmed"),
                body:
                    _t(
                        "Do not hand over another refund. Reconnect and validate this same order to recover its status."
                    ) +
                    " " +
                    (error.data?.message || error.message),
            });
        } finally {
            this.env.services.ui.unblock();
        }
    },
});

patch(PosOrder.prototype, {
    serialize(options = {}) {
        const values = super.serialize(...arguments);
        if (options.orm) {
            for (const name of [
                "dte_return_target_document_id",
                "dte_return_operation",
                "dte_return_accounting_mode",
                "dte_pos_mapping_state",
            ]) {
                delete values[name];
            }
        }
        return values;
    },
    export_for_printing(baseUrl, headerData) {
        return {
            ...super.export_for_printing(baseUrl, headerData),
            dte_return_status: this.uiState.dteReturnReceipt,
        };
    },
});

patch(PosOrderline.prototype, {
    serialize(options = {}) {
        const values = super.serialize(...arguments);
        if (options.orm) {
            delete values.dte_return_origin_line_number;
            delete values.dte_return_map_artifact_id;
        }
        return values;
    },
});
