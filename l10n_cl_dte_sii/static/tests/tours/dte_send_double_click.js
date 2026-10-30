/** @odoo-module **/

import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("l10n_cl_dte_send_double_click_tour", {
    steps: () => [
        {
            content: "send the same DTE twice while the first RPC is pending",
            trigger: '.o_form_view button[name="action_l10n_cl_dte_send"]',
            run: (button) => {
                button.click();
                button.click();
            },
        },
        {
            content: "the invoice reaches the pending response state once",
            trigger: '.o_form_view button[name="action_l10n_cl_dte_check_status"]',
        },
    ],
});
