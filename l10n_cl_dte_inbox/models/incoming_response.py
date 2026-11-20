# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
import json

from odoo import _, api, fields, models
from odoo.addons.l10n_cl_dte_gateway.services.certification_auth import (
    SiiTransportError,
)
from odoo.addons.l10n_cl_dte_gateway.services.claim_registry import (
    ACTIONS,
    SiiClaimRegistryClient,
)
from odoo.exceptions import UserError, ValidationError

RESPONSE_ACTIONS = [
    ("ACD", "ACD - Accept the document content"),
    ("RCD", "RCD - Claim the document content"),
    ("ERM", "ERM - Acknowledge receipt of goods or services"),
    ("RFP", "RFP - Claim partial lack of goods"),
    ("RFT", "RFT - Claim total lack of goods"),
]


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
    sii_message = fields.Char(string="SII Message", readonly=True)
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
        if response_code.strip() not in ACTIONS:
            raise UserError(_("Choose ACD, RCD, ERM, RFP or RFT."))
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

    def _write_internal(self, values):
        return super().write(values)

    def _check_manager(self):
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise UserError(_("Only a DTE manager can send an incoming DTE response."))

    def action_enqueue(self):
        """Queue the registration of the action in the SII claim registry."""
        self._check_manager()
        for response in self:
            if response.state != "pending":
                raise UserError(_("Only a pending response can be sent."))
            response.with_delay(identity_key=f"dte-claim-{response.id}")._job_send()
        return True

    def action_poll(self):
        """Confirm an uncertain registration from the DTE event history."""
        self._check_manager()
        for response in self:
            if response.state not in {"submitted", "outcome_unknown"}:
                raise UserError(_("Only an uncertain response can be checked."))
            response._job_poll()
        return True

    def _client(self):
        return SiiClaimRegistryClient(self.environment)

    def _job_send(self):
        self.ensure_one()
        self.env.cr.execute(
            "SELECT state FROM l10n_cl_incoming_dte_response WHERE id = %s FOR UPDATE",
            [self.id],
        )
        self.invalidate_recordset(["state"])
        if self.state != "pending":
            return True
        document = self.incoming_document_id
        if self.environment == "demo":
            return self._finish("accepted", "simulated", "Demo: not sent to SII")
        self._write_internal({"state": "sending"})
        attempt = self._start_attempt("register")
        try:
            result = self._client().register_action(
                document.company_id,
                document.issuer_vat,
                document.document_code,
                document.folio,
                self.response_code,
            )
        except SiiTransportError as error:
            state = "outcome_unknown" if error.dispatched else "error"
            attempt._finish_internal(
                "unknown" if error.dispatched else "failed", diagnostics=str(error)
            )
            return self._finish(state, False, str(error))
        except ValidationError as error:
            attempt._finish_internal("unknown", diagnostics=str(error))
            return self._finish("outcome_unknown", False, str(error))
        attempt._finish_internal(
            "completed",
            transport_code=str(result["code"]),
            diagnostics=result["description"],
            raw_response=result["raw_response"],
        )
        return self._finish(
            "accepted" if result["success"] else "rejected",
            str(result["code"]),
            result["description"],
        )

    def _job_poll(self):
        self.ensure_one()
        document = self.incoming_document_id
        attempt = self._start_attempt("events")
        try:
            result = self._client().list_events(
                document.company_id,
                document.issuer_vat,
                document.document_code,
                document.folio,
            )
        except (SiiTransportError, ValidationError) as error:
            attempt._finish_internal("failed", diagnostics=str(error))
            return False
        attempt._finish_internal(
            "completed",
            transport_code=str(result["code"]),
            diagnostics=result["description"],
            raw_response=result["raw_response"],
        )
        if any(event["code"] == self.response_code for event in result["events"]):
            return self._finish("accepted", "event", _("Confirmed by SII events"))
        return False

    def _start_attempt(self, operation):
        return self.env["l10n_cl.incoming.dte.response.attempt"]._create_internal(
            {
                "response_id": self.id,
                "operation": operation,
                "started_at": fields.Datetime.now(),
                "state": "running",
                "request_digest": self.request_digest,
            }
        )

    def _finish(self, state, reference, message):
        previous = self.state
        self._write_internal(
            {
                "state": state,
                "external_reference": reference,
                "sii_message": (message or "")[:250] or False,
            }
        )
        document = self.incoming_document_id
        document._create_event(
            f"response_{state}", document.state, document.state, self.request_digest
        )
        return previous != state


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

    @api.model
    def _create_internal(self, values):
        return super(IncomingDteResponseAttempt, self.sudo()).create([values])

    def _finish_internal(
        self, state, transport_code=None, diagnostics=None, raw_response=None
    ):
        values = {
            "state": state,
            "finished_at": fields.Datetime.now(),
            "transport_code": transport_code or False,
            "diagnostics": (diagnostics or "")[:4000] or False,
        }
        if raw_response:
            values["raw_response"] = base64.b64encode(raw_response)
            values["response_digest"] = hashlib.sha256(raw_response).hexdigest()
        return super(IncomingDteResponseAttempt, self.sudo()).write(values)

    def write(self, values):  # pylint: disable=method-required-super  # noqa: ARG002
        raise UserError(_("Incoming DTE response attempts are immutable."))

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("Incoming DTE response attempts must be retained."))
