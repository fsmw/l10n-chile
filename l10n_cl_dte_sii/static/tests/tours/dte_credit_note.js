/* Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
 * License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl). */
/** @odoo-module **/

import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("l10n_cl_dte_credit_note_from_invoice", {
    steps: () => [
        {
            content: "open the credit note wizard from the posted invoice",
            trigger: ".o_form_view button[name='action_reverse']",
            run: "click",
        },
        {
            content: "provide the reason for the credit note",
            trigger: ".modal .o_field_widget[name='reason'] input",
            run: "edit Anulación por error de monto",
        },
        {
            content: "create the draft credit note",
            trigger: ".modal button[name='refund_moves']",
            run: "click",
        },
        {
            content: "show the generated credit note",
            trigger: ".o_form_view button[name='action_post']",
        },
    ],
});
