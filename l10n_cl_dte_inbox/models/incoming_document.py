# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
import json
import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from psycopg2.errors import SerializationFailure, UniqueViolation

from ..services.incoming_parser import parse_incoming_dte
from ..services.incoming_validation import IncomingValidationService, normalize_vat
from .incoming_import import CHECK_STATES, ENVIRONMENTS


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
        ENVIRONMENTS,
        required=True,
        readonly=True,
        index=True,
        default=lambda self: self.env.company.dte_sii_environment,
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
            ("validated", "Locally Validated"),
            ("invalid", "Invalid"),
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
    schema_state = fields.Selection(CHECK_STATES, default="not_checked", readonly=True)
    schema_name = fields.Char(readonly=True)
    signature_state = fields.Selection(
        CHECK_STATES, default="not_checked", readonly=True
    )
    ted_state = fields.Selection(CHECK_STATES, default="not_checked", readonly=True)
    signer_certificate_fingerprint = fields.Char(readonly=True)
    signer_trust_state = fields.Selection(
        [("unverified", "Trust Not Verified")], default="unverified", readonly=True
    )
    diagnostics = fields.Text(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    validated_at = fields.Datetime(readonly=True)
    import_line_ids = fields.One2many(
        "l10n_cl.incoming.dte.import.line", "incoming_document_id", readonly=True
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
    def _import_xml(
        self, content, channel="operator_imported", filename=False, validation=None
    ):
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(_("Only a DTE manager can import incoming DTEs."))
        if channel not in {"sii_portal", "email", "operator_imported"}:
            raise ValidationError(_("The incoming DTE source channel is invalid."))
        if not isinstance(content, bytes) or not content:
            raise ValidationError(_("The incoming DTE XML is empty."))
        parsed = parse_incoming_dte(content, translate=self.env._)
        company = self.env.company
        expected_receiver = _normalize_vat(company.partner_id.vat)
        if (
            not expected_receiver
            or _normalize_vat(parsed["receiver_vat"]) != expected_receiver
        ):
            raise ValidationError(
                _("The DTE receiver does not match the current company.")
            )
        issue_date = fields.Date.to_date(parsed["issue_date"])
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
        norm_issuer = normalize_vat(parsed["issuer_vat"])
        # Old records retain their original RUT formatting; compare identities
        # canonically without rewriting historical XML or metadata.
        identity_matches = self.search(
            [
                ("company_id", "=", company.id),
                ("sii_environment", "=", company.dte_sii_environment),
                ("document_code", "=", parsed["document_code"]),
                ("folio", "=", parsed["folio"]),
            ]
        ).filtered(lambda record: normalize_vat(record.issuer_vat) == norm_issuer)
        validation = (
            validation
            or IncomingValidationService(self.env).inspect(content)["documents"][0]
        )
        if identity_matches:
            if (
                len(identity_matches) == 1
                and identity_matches._identity_digest() == validation["identity_digest"]
            ):
                return identity_matches
            raise ValidationError(
                _("This DTE identity already exists with different signed XML.")
            )
        try:
            with self.env.cr.savepoint():
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
                            "sii_environment": company.dte_sii_environment,
                            "channel": channel,
                            "received_at": fields.Datetime.now(),
                            "issuer_vat": norm_issuer[:-1] + "-" + norm_issuer[-1:],
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
        except UniqueViolation as error:
            raise SerializationFailure(
                "Retry concurrent incoming DTE import"
            ) from error
        record._create_event("imported", "", "received")
        record._apply_validation(validation)
        return record

    def _identity_digest(self):
        self.ensure_one()
        from lxml import etree

        from ..services.incoming_parser import extract_incoming_dtes

        content = base64.b64decode(self.raw_xml, validate=True)
        if hashlib.sha256(content).hexdigest() != self.xml_sha256:
            raise ValidationError(
                _("The stored incoming XML digest does not match its evidence.")
            )
        _root, nodes = extract_incoming_dtes(content, self.env._)
        if len(nodes) != 1:
            raise ValidationError(_("The stored incoming record must contain one DTE."))
        return hashlib.sha256(
            etree.tostring(nodes[0], method="c14n", exclusive=True)
        ).hexdigest()

    def _apply_validation(self, result):
        self.ensure_one()
        valid = not result["diagnostics"] and all(
            result[key] == "valid"
            for key in ("schema_state", "signature_state", "ted_state")
        )
        self._write_internal(
            {
                "xml_validation_state": "validated" if valid else "invalid",
                "schema_state": result["schema_state"],
                "schema_name": result["schema_name"],
                "signature_state": result["signature_state"],
                "ted_state": result["ted_state"],
                "signer_certificate_fingerprint": result["signer_fingerprint"],
                "validated_at": fields.Datetime.now(),
                "diagnostics": json.dumps(
                    result["diagnostics"], ensure_ascii=False, indent=2
                ),
            }
        )
        self._create_event(
            "local_validation_valid" if valid else "local_validation_invalid",
            self.state,
            self.state,
            self.xml_sha256,
        )

    def action_validate(self):
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(_("Only a DTE manager can validate incoming DTEs."))
        for record in self:
            content = base64.b64decode(record.raw_xml, validate=True)
            if hashlib.sha256(content).hexdigest() != record.xml_sha256:
                raise ValidationError(
                    _("The stored incoming XML digest does not match its evidence.")
                )
            result = IncomingValidationService(self.env).inspect(content)
            if len(result["documents"]) != 1:
                raise ValidationError(
                    _("The stored incoming record must contain one DTE.")
                )
            item = result["documents"][0]
            item["diagnostics"] += result["diagnostics"]
            record._apply_validation(item)
        return True

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
