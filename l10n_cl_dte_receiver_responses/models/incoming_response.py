# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .incoming_exchange import SIGNING_ENVIRONMENTS

_logger = logging.getLogger(__name__)

# SII registry action -> emailed response kind and ResultadoDTE status.
REGISTRY_EXCHANGES = {
    "ACD": ("result", "0"),
    "RCD": ("result", "2"),
    "ERM": ("receipt", "0"),
}


class IncomingDteResponse(models.Model):
    _inherit = "l10n_cl.incoming.dte.response"

    exchange_kind = fields.Selection(
        [("result", "Commercial Result"), ("receipt", "Receipt of Goods")],
        compute="_compute_exchange",
    )
    exchange_line_id = fields.Many2one(
        "l10n_cl.incoming.dte.exchange.line", compute="_compute_exchange"
    )

    @api.depends("response_code", "incoming_document_id.exchange_line_ids")
    def _compute_exchange(self):
        for response in self:
            kind = REGISTRY_EXCHANGES.get(response.response_code, (False,))[0]
            response.exchange_kind = kind
            response.exchange_line_id = (
                response.incoming_document_id.exchange_line_ids.filtered(
                    lambda line, kind=kind: line.kind == kind
                )[:1]
                if kind
                else False
            )

    def action_open_exchange_wizard(self):
        """Offer the emailed response matching this registered SII action."""
        self.ensure_one()
        if self.state != "accepted" or not self.exchange_kind:
            raise UserError(
                _("Only a registered ACD, RCD or ERM action has an emailed response.")
            )
        kind, result_state = REGISTRY_EXCHANGES[self.response_code]
        action = self.incoming_document_id.with_context(
            exchange_kind=kind
        ).action_open_exchange_wizard()
        action["context"].update(
            default_result_state=result_state,
            default_gloss=self.response_text,
            default_registry_response_id=self.id,
        )
        return action

    def _finish(self, state, reference, message):
        changed = super()._finish(state, reference, message)
        if changed and state == "accepted":
            self._auto_send_exchange()
        return changed

    def _auto_send_exchange(self):
        """Email the matching response only when the company enabled it."""
        self.ensure_one()
        document = self.incoming_document_id
        if (
            not document.company_id.l10n_cl_dte_exchange_auto_send
            or self.response_code not in REGISTRY_EXCHANGES
            or self.environment not in SIGNING_ENVIRONMENTS
        ):
            return False
        kind, result_state = REGISTRY_EXCHANGES[self.response_code]
        try:
            with self.env.cr.savepoint():
                exchanges = self.env[
                    "l10n_cl.incoming.dte.exchange"
                ]._prepare_documents(
                    kind,
                    document,
                    state=result_state,
                    gloss=self.response_text,
                    registry_response=self,
                    trigger="registry_auto",
                )
                exchanges._email_pending()
        except (UserError, ValidationError) as error:
            # The SII registration already succeeded; keep it and leave the
            # emailed response for the user to retry.
            _logger.warning(
                "Could not email the exchange response of %s: %s", document.name, error
            )
            document._create_event(
                "exchange_auto_send_failed",
                document.state,
                document.state,
                self.request_digest,
            )
            return False
        return True
