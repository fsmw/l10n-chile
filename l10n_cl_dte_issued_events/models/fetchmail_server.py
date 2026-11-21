# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import email
import email.policy
import logging

from odoo import models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services.receiver_response_parser import is_response_xml

_logger = logging.getLogger(__name__)


class FetchmailServer(models.Model):
    _inherit = "fetchmail.server"

    def _import_dte_message(self, raw_message):
        """Also record the RespuestaDTE and EnvioRecibos sent for our DTEs."""
        imported = super()._import_dte_message(raw_message)
        self._import_receiver_responses(raw_message)
        return imported

    def _import_receiver_responses(self, raw_message):
        self.ensure_one()
        message = email.message_from_bytes(raw_message, policy=email.policy.SMTP)
        model = (
            self.env["l10n_cl.dte.receiver.response"]
            .with_user(self.l10n_cl_dte_user_id)
            .with_company(self.l10n_cl_dte_company_id)
        )
        responses = model.browse()
        for part in message.walk():
            filename = part.get_filename() or ""
            if not filename.lower().endswith(".xml"):
                continue
            content = part.get_payload(decode=True)
            if not content or not is_response_xml(content):
                continue
            try:
                with self.env.cr.savepoint():
                    responses |= model._import_xml(content, filename)
            except (AccessError, UserError, ValidationError) as error:
                # Refused files, such as a bad signature, are not stored.
                _logger.info("Refused receiver response %s: %s", filename, error)
        return responses
