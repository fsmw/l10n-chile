# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services.incoming_parser import parse_incoming_dte


def _normalize_vat(value):
    return re.sub(r"[^0-9Kk]", "", value or "").upper()


class IncomingDte(models.Model):
    _name = "l10n_cl.incoming.dte"
    _description = "Incoming Chilean Electronic Tax Document"
    _order = "received_at desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, readonly=True, copy=False, index=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        ondelete="restrict",
    )
    sii_environment = fields.Selection(
        related="company_id.dte_sii_environment", store=True, readonly=True, index=True
    )
    channel = fields.Selection(
        [
            ("sii_portal", "SII Portal"),
            ("sii_webservice", "SII Web Service"),
            ("email", "Email"),
            ("operator_imported", "Operator Import"),
        ],
        required=True,
        readonly=True,
        index=True,
    )
    received_at = fields.Datetime(
        required=True, readonly=True, default=fields.Datetime.now
    )
    issuer_vat = fields.Char(required=True, readonly=True, index=True)
    receiver_vat = fields.Char(required=True, readonly=True, index=True)
    document_code = fields.Char(required=True, readonly=True, index=True)
    folio = fields.Integer(required=True, readonly=True, index=True)
    issue_date = fields.Date(required=True, readonly=True)
    raw_xml = fields.Binary(
        required=True,
        attachment=True,
        readonly=True,
        copy=False,
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    filename = fields.Char(readonly=True, copy=False)
    xml_sha256 = fields.Char(required=True, readonly=True, index=True, copy=False)
    xml_validation_state = fields.Selection(
        [
            ("parsed", "Identity Parsed"),
            ("signature_unverified", "Signature Unverified"),
        ],
        required=True,
        default="parsed",
        readonly=True,
    )
    state = fields.Selection(
        [("received", "Received"), ("reviewed", "Reviewed")],
        required=True,
        default="received",
        readonly=True,
        index=True,
    )
    event_ids = fields.One2many("l10n_cl.incoming.dte.event", "incoming_document_id")
    response_ids = fields.One2many(
        "l10n_cl.incoming.dte.response", "incoming_document_id"
    )

    _sql_constraints = [
        (
            "incoming_xml_company_environment_uniq",
            "unique(company_id, sii_environment, xml_sha256)",
            "This incoming XML is already registered for the company and environment.",
        ),
        (
            "incoming_identity_company_environment_uniq",
            "unique(company_id, sii_environment, issuer_vat, document_code, folio)",
            "This incoming DTE identity is already registered.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super  # noqa: ARG002
        raise UserError(
            _("Incoming DTEs are registered through the XML import action.")
        )

    @api.model
    def _import_xml(self, content, channel="operator_imported", filename=False):
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(_("Only a DTE manager can import incoming DTEs."))
        if channel not in {"sii_portal", "email", "operator_imported"}:
            raise ValidationError(_("The incoming DTE source channel is invalid."))
        if not isinstance(content, bytes) or not content:
            raise ValidationError(_("The incoming DTE XML is empty."))
        parsed = parse_incoming_dte(content, translate=_)
        company = self.env.company
        expected_receiver = _normalize_vat(company.partner_id.vat)
        if (
            not expected_receiver
            or _normalize_vat(parsed["receiver_vat"]) != expected_receiver
        ):
            raise ValidationError(
                _("The DTE receiver does not match the current company.")
            )
        try:
            issue_date = fields.Date.to_date(parsed["issue_date"])
        except (TypeError, ValueError) as error:
            raise ValidationError(_("The DTE issue date is invalid.")) from error
        if not issue_date:
            raise ValidationError(_("The DTE issue date is invalid."))
        digest = hashlib.sha256(content).hexdigest()
        existing = self.search(
            [
                ("company_id", "=", company.id),
                ("sii_environment", "=", company.dte_sii_environment),
                ("xml_sha256", "=", digest),
            ],
            limit=1,
        )
        if existing:
            return existing
        record = (
            super(IncomingDte, self.sudo())
            .create(
                {
                    "name": _(
                        "Incoming DTE %(code)s / %(folio)s",
                        code=parsed["document_code"],
                        folio=parsed["folio"],
                    ),
                    "company_id": company.id,
                    "channel": channel,
                    "received_at": fields.Datetime.now(),
                    "issuer_vat": parsed["issuer_vat"],
                    "receiver_vat": parsed["receiver_vat"],
                    "document_code": parsed["document_code"],
                    "folio": parsed["folio"],
                    "issue_date": issue_date,
                    "raw_xml": base64.b64encode(content),
                    "filename": filename or "incoming-dte.xml",
                    "xml_sha256": digest,
                    "xml_validation_state": "signature_unverified",
                }
            )
            .with_user(self.env.user)
        )
        record._create_event("imported", "", "received")
        return record

    def _create_event(self, event_code, from_state, to_state, payload_digest=False):
        self.ensure_one()
        return self.env["l10n_cl.incoming.dte.event"]._create_for_document(
            self, event_code, from_state, to_state, payload_digest
        )

    def action_open(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Incoming DTE"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_mark_reviewed(self):
        for record in self:
            if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
                raise AccessError(_("Only a DTE manager can review incoming DTEs."))
            if record.state != "received":
                raise UserError(_("Only received DTEs can be marked as reviewed."))
            record._write_internal({"state": "reviewed"})
            record._create_event("reviewed", "received", "reviewed")
        return True

    def action_create_response(self):
        self.ensure_one()
        if self.state != "reviewed":
            raise UserError(_("Review the incoming DTE before preparing a response."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Prepare Incoming DTE Response"),
            "res_model": "l10n_cl.incoming.dte.response.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_incoming_document_id": self.id},
        }

    def _write_internal(self, values):
        return super().write(values)

    def write(self, values):  # pylint: disable=method-required-super  # noqa: ARG002
        raise UserError(_("Incoming DTE evidence is immutable."))

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("Incoming DTE evidence must be retained."))


class IncomingDteEvent(models.Model):
    _name = "l10n_cl.incoming.dte.event"
    _description = "Incoming DTE Audit Event"
    _order = "occurred_at desc, id desc"

    incoming_document_id = fields.Many2one(
        "l10n_cl.incoming.dte", required=True, index=True, ondelete="restrict"
    )
    company_id = fields.Many2one(
        related="incoming_document_id.company_id", store=True, index=True
    )
    event_code = fields.Char(required=True, readonly=True)
    occurred_at = fields.Datetime(
        required=True, readonly=True, default=fields.Datetime.now
    )
    actor_id = fields.Many2one(
        "res.users", required=True, readonly=True, default=lambda self: self.env.user
    )
    from_state = fields.Char(readonly=True)
    to_state = fields.Char(readonly=True)
    correlation_id = fields.Char(readonly=True, index=True)
    payload_digest = fields.Char(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super  # noqa: ARG002
        raise UserError(_("Incoming DTE events are created by workflow actions."))

    @api.model
    def _create_for_document(
        self, document, code, previous, current, payload_digest=False
    ):
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(_("Only a DTE manager can append incoming DTE events."))
        return (
            super(IncomingDteEvent, self.sudo())
            .create(
                {
                    "incoming_document_id": document.id,
                    "event_code": code,
                    "from_state": previous,
                    "to_state": current,
                    "correlation_id": document.xml_sha256,
                    "payload_digest": payload_digest,
                }
            )
            .with_user(self.env.user)
        )

    def write(self, values):  # pylint: disable=method-required-super  # noqa: ARG002
        raise UserError(_("Incoming DTE audit events are immutable."))

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("Incoming DTE audit events must be retained."))
