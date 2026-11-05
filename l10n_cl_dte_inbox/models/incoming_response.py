# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hashlib
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class IncomingDteResponse(models.Model):
    _name = "l10n_cl.incoming.dte.response"
    _description = "Incoming DTE Response"
    _order = "create_date desc, id desc"

    incoming_document_id = fields.Many2one(
        "l10n_cl.incoming.dte", required=True, index=True, ondelete="restrict"
    )
    company_id = fields.Many2one(
        related="incoming_document_id.company_id", store=True, index=True
    )
    response_code = fields.Char(required=True, readonly=True)
    response_text = fields.Char(required=True, readonly=True)
    contract_reference = fields.Char(required=True, readonly=True)
    environment = fields.Selection(
        related="incoming_document_id.sii_environment", store=True
    )
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("sending", "Sending"),
            ("submitted", "Submitted"),
            ("accepted", "Accepted"),
            ("rejected", "Rejected"),
            ("outcome_unknown", "Outcome Unknown"),
            ("error", "Error"),
        ],
        required=True,
        default="pending",
        readonly=True,
        index=True,
    )
    idempotency_key = fields.Char(required=True, readonly=True, index=True)
    request_digest = fields.Char(required=True, readonly=True, index=True)
    response_digest = fields.Char(readonly=True)
    external_reference = fields.Char(readonly=True, index=True)
    attempt_ids = fields.One2many(
        "l10n_cl.incoming.dte.response.attempt", "response_id"
    )

    _sql_constraints = [
        (
            "response_idempotency_uniq",
            "unique(idempotency_key)",
            "This incoming DTE response is already prepared.",
        )
    ]

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super  # noqa: ARG002
        raise UserError(
            _("Incoming DTE responses are created from a reviewed document.")
        )

    @api.model
    def _create_from_document(
        self, document, response_code, response_text, contract_reference
    ):
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise UserError(
                _("Only a DTE manager can prepare an incoming DTE response.")
            )
        if document.state != "reviewed":
            raise UserError(_("Only a reviewed incoming DTE can have a response."))
        if not all((response_code, response_text, contract_reference)):
            raise UserError(
                _("Response code, text, and contract reference are required.")
            )
        material = json.dumps(
            {
                "incoming_xml_sha256": document.xml_sha256,
                "response_code": response_code.strip(),
                "response_text": response_text.strip(),
                "contract_reference": contract_reference.strip(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        digest = hashlib.sha256(material).hexdigest()
        existing = self.search(
            [
                ("incoming_document_id", "=", document.id),
                ("idempotency_key", "=", digest),
            ],
            limit=1,
        )
        if existing:
            return existing
        values = {
            "incoming_document_id": document.id,
            "response_code": response_code.strip(),
            "response_text": response_text.strip(),
            "contract_reference": contract_reference.strip(),
            "idempotency_key": digest,
            "request_digest": digest,
        }
        response = (
            super(IncomingDteResponse, self.sudo())
            .create(values)
            .with_user(self.env.user)
        )
        document._create_event(
            "response_prepared", document.state, document.state, digest
        )
        return response

    def write(self, values):  # pylint: disable=method-required-super  # noqa: ARG002
        raise UserError(_("Incoming DTE response history is immutable."))

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("Incoming DTE response history must be retained."))

    def action_enqueue(self):
        raise UserError(
            _(
                "Response transmission is disabled until the current SII response "
                "contract and transport are verified."
            )
        )

    def action_poll(self):
        raise UserError(
            _(
                "Response status queries are disabled until the current SII response "
                "contract and transport are verified."
            )
        )


class IncomingDteResponseAttempt(models.Model):
    _name = "l10n_cl.incoming.dte.response.attempt"
    _description = "Incoming DTE Response Transmission Attempt"
    _order = "started_at desc, id desc"

    response_id = fields.Many2one(
        "l10n_cl.incoming.dte.response", required=True, index=True, ondelete="restrict"
    )
    company_id = fields.Many2one(
        related="response_id.company_id", store=True, index=True
    )
    operation = fields.Char(required=True, readonly=True)
    started_at = fields.Datetime(required=True, readonly=True)
    finished_at = fields.Datetime(readonly=True)
    state = fields.Selection(
        [
            ("running", "Running"),
            ("completed", "Completed"),
            ("unknown", "Unknown"),
            ("failed", "Failed"),
        ],
        required=True,
        readonly=True,
    )
    request_digest = fields.Char(required=True, readonly=True)
    response_digest = fields.Char(readonly=True)
    transport_code = fields.Char(readonly=True)
    diagnostics = fields.Text(readonly=True)
    raw_response = fields.Binary(
        attachment=True, readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super  # noqa: ARG002
        raise UserError(_("Response attempts are created by the transmission service."))

    def write(self, values):  # pylint: disable=method-required-super  # noqa: ARG002
        raise UserError(_("Incoming DTE response attempts are immutable."))

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("Incoming DTE response attempts must be retained."))
