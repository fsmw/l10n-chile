# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.l10n_cl_dte_gateway.services.certification_auth import (
    SiiTransportError,
)
from odoo.addons.l10n_cl_dte_gateway.services.claim_registry import (
    SiiClaimRegistryClient,
)

from ..services.receiver_states import (
    FINAL_CHECK_GRACE,
    LEGAL_WINDOW,
    RECEIVER_STATES,
    REGISTRY_DOCUMENT_CODES,
    REGISTRY_ENVIRONMENTS,
    chile_datetime_to_utc,
    derive_receiver_state,
    next_check,
    registry_tracking_finished,
)

_logger = logging.getLogger(__name__)
CRON_BATCH = 50


class ElectronicDocument(models.Model):
    _inherit = "l10n_cl.electronic.document"

    receiver_event_ids = fields.One2many(
        "l10n_cl.dte.receiver.event", "document_id", string="Receiver Responses"
    )
    receiver_state = fields.Selection(
        RECEIVER_STATES,
        compute="_compute_receiver_state",
        help="Receiver answer derived from the SII registry and emailed responses.",
    )
    receiver_tracked = fields.Boolean(
        compute="_compute_receiver_tracked",
        help="Whether the SII acceptance and claim registry is followed.",
    )
    receiver_reception_at = fields.Datetime(
        string="SII Reception",
        compute="_compute_receiver_reception_at",
        store=True,
        help="First fiscal submission event; the legal window starts here.",
    )
    receiver_deadline = fields.Datetime(
        string="Claim Deadline",
        compute="_compute_receiver_reception_at",
        store=True,
        help="Eight calendar days after SII reception.",
    )
    receiver_next_check_at = fields.Datetime(
        string="Next Registry Check", readonly=True, copy=False, index=True
    )
    receiver_last_check_at = fields.Datetime(
        string="Last Registry Check", readonly=True, copy=False
    )
    receiver_check_count = fields.Integer(readonly=True, copy=False)
    receiver_check_message = fields.Char(
        string="Last Registry Answer", readonly=True, copy=False
    )
    receiver_tracking_done = fields.Boolean(readonly=True, copy=False, index=True)

    @api.depends("event_ids.to_state", "event_ids.occurred_at")
    def _compute_receiver_reception_at(self):
        for document in self:
            dates = (
                document.sudo()
                .event_ids.filtered(
                    lambda event: event.state_domain == "fiscal"
                    and event.to_state in {"submitted", "accepted"}
                )
                .mapped("occurred_at")
            )
            document.receiver_reception_at = min(dates) if dates else False
            document.receiver_deadline = min(dates) + LEGAL_WINDOW if dates else False

    @api.depends("sii_environment", "fiscal_state", "document_code")
    def _compute_receiver_tracked(self):
        for document in self:
            document.receiver_tracked = (
                document.sii_environment in REGISTRY_ENVIRONMENTS
                and document.fiscal_state == "accepted"
                and document.document_code in REGISTRY_DOCUMENT_CODES
            )

    @api.depends(
        "receiver_event_ids.source",
        "receiver_event_ids.event_code",
        "receiver_tracked",
        "receiver_deadline",
    )
    def _compute_receiver_state(self):
        now = fields.Datetime.now()
        for document in self:
            events = document.sudo().receiver_event_ids
            document.receiver_state = derive_receiver_state(
                [(event.source, event.event_code) for event in events],
                document.receiver_deadline,
                now,
                document.receiver_tracked,
            )

    def action_check_receiver_response(self):
        """Query the SII registry now for the selected issued DTEs."""
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(
                self.env._("Only a DTE manager can query receiver responses.")
            )
        self.check_access("read")
        for document in self:
            if not document.receiver_tracked:
                raise UserError(
                    self.env._(
                        "Only SII-accepted invoices in Certification or Production "
                        "are followed in the SII acceptance registry."
                    )
                )
            error = document._check_receiver_registry()
            if error:
                raise UserError(error)
        return True

    @api.model
    def _cron_check_receiver_responses(self, limit=CRON_BATCH):
        """Poll due documents of the legal window, then stop following them."""
        now = fields.Datetime.now()
        documents = self.sudo().search(
            [
                ("sii_environment", "in", REGISTRY_ENVIRONMENTS),
                ("fiscal_state", "=", "accepted"),
                ("document_code", "in", REGISTRY_DOCUMENT_CODES),
                ("receiver_tracking_done", "=", False),
                ("receiver_deadline", ">=", now - FINAL_CHECK_GRACE),
                "|",
                ("receiver_next_check_at", "=", False),
                ("receiver_next_check_at", "<=", now),
            ],
            order="receiver_next_check_at asc nulls first, id asc",
            limit=limit,
        )
        for document in documents:
            try:
                with self.env.cr.savepoint():
                    document._check_receiver_registry()
            except Exception:  # pylint: disable=broad-except
                # One failing document must not block the others of the batch.
                _logger.warning(
                    "Receiver registry check failed for DTE %s", document.id
                )
        return True

    def _check_receiver_registry(self):
        """List the registry events of one issued DTE; return an error or False."""
        self.ensure_one()
        now = fields.Datetime.now()
        company = self.company_id
        error = False
        try:
            result = self._receiver_registry_client().list_events(
                company, company.vat, self.document_code, self.folio
            )
        except (SiiTransportError, ValidationError, UserError) as failure:
            error = str(failure)
            message = error
        else:
            self._store_receiver_events(
                "sii", [self._registry_event_values(item) for item in result["events"]]
            )
            message = result["description"] or str(result["code"])
        self._schedule_receiver_check(now, message)
        return error

    def _receiver_registry_client(self):
        return SiiClaimRegistryClient(self.sii_environment)

    @api.model
    def _registry_event_values(self, item):
        return {
            "code": (item.get("code") or "").strip().upper(),
            "description": item.get("description") or False,
            "date_key": (item.get("date") or "").strip() or "undated",
            "date": chile_datetime_to_utc(item.get("date")),
            "actor_vat": item.get("responsible") or False,
        }

    def _schedule_receiver_check(self, now, message):
        count = self.receiver_check_count + 1
        upcoming, finished = next_check(now, count, self.receiver_deadline)
        codes = (
            self.sudo()
            .receiver_event_ids.filtered(lambda event: event.source == "sii")
            .mapped("event_code")
        )
        finished = finished or registry_tracking_finished(codes)
        self.sudo().write(
            {
                "receiver_last_check_at": now,
                "receiver_check_count": count,
                "receiver_check_message": (message or "")[:250] or False,
                "receiver_next_check_at": False if finished else upcoming,
                "receiver_tracking_done": finished,
            }
        )

    def _store_receiver_events(self, source, values_list, response=None):
        """Record new events once per (source, code, date) and flag claims."""
        self.ensure_one()
        known = {
            (event.event_code, event.event_date_key)
            for event in self.sudo().receiver_event_ids
            if event.source == source
        }
        new_values = []
        for values in values_list:
            key = (values["code"], values["date_key"])
            if not values["code"] or key in known:
                continue
            known.add(key)
            new_values.append(
                {
                    "document_id": self.id,
                    "source": source,
                    "event_code": values["code"][:32],
                    "description": (values.get("description") or "")[:1000] or False,
                    "event_date_key": values["date_key"][:64],
                    "event_date": values.get("date") or False,
                    "actor_vat": (values.get("actor_vat") or "")[:32] or False,
                    "response_id": response.id if response else False,
                }
            )
        events = self.env["l10n_cl.dte.receiver.event"]._create_internal(new_values)
        claims = events.filtered("is_claim")
        if claims:
            self._notify_receiver_claims(claims)
        return events

    def _notify_receiver_claims(self, claims):
        """Ask the invoice responsible to review a claim; never touch accounting."""
        move = self.sudo().source_move_id
        if not move:
            return False
        user = move.invoice_user_id or move.create_uid
        for claim in claims:
            move.activity_schedule(
                "mail.mail_activity_data_todo",
                user_id=user.id,
                summary=self.env._(
                    "Receiver claim %(code)s on DTE %(type)s-%(folio)s",
                    code=claim.event_code,
                    type=self.document_code,
                    folio=self.folio,
                ),
                note=claim.description or claim.event_code,
            )
        return True
