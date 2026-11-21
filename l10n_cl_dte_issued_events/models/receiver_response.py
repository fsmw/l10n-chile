# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib

from odoo import api, fields, models
from odoo.exceptions import AccessError

from odoo.addons.l10n_cl_dte_inbox.services.incoming_validation import normalize_vat
from odoo.addons.l10n_cl_dte_signing.services.signature_service import (
    SignatureService,
)

from ..services.receiver_response_parser import parse_receiver_response
from ..services.receiver_states import chile_datetime_to_utc


class DteReceiverResponse(models.Model):
    _name = "l10n_cl.dte.receiver.response"
    _description = "Receiver Response Emailed for Issued DTEs"
    _order = "create_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(string="File Name", required=True, readonly=True)
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, index=True
    )
    kind = fields.Selection(
        [
            ("respuesta_dte", "RespuestaDTE"),
            ("envio_recibos", "EnvioRecibos (Goods Receipt)"),
        ],
        required=True,
        readonly=True,
    )
    channel = fields.Selection(
        [("email", "Email"), ("upload", "Upload")],
        required=True,
        default="email",
        readonly=True,
    )
    content = fields.Binary(
        attachment=True, readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    sha256 = fields.Char(required=True, readonly=True, index=True)
    byte_count = fields.Integer(readonly=True)
    responder_vat = fields.Char(string="Responder RUT", readonly=True)
    signed_at = fields.Char(string="Signature Timestamp", readonly=True)
    match_state = fields.Selection(
        [
            ("matched", "Matched"),
            ("partial", "Partially Matched"),
            ("unmatched", "Not Our DTE"),
        ],
        required=True,
        readonly=True,
        index=True,
    )
    diagnostics = fields.Text(readonly=True)
    event_ids = fields.One2many("l10n_cl.dte.receiver.event", "response_id")
    document_ids = fields.Many2many(
        "l10n_cl.electronic.document", compute="_compute_document_ids"
    )

    _sql_constraints = [
        (
            "receiver_response_sha_uniq",
            "unique(company_id, sha256)",
            "This receiver response file is already recorded.",
        )
    ]

    @api.depends("event_ids.document_id")
    def _compute_document_ids(self):
        for response in self:
            response.document_ids = response.event_ids.document_id

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super  # noqa: ARG002
        raise AccessError(
            self.env._("Receiver responses are recorded from received XML files.")
        )

    def write(self, vals):  # pylint: disable=method-required-super  # noqa: ARG002
        raise AccessError(self.env._("Receiver responses are immutable."))

    def unlink(self):  # pylint: disable=method-required-super
        raise AccessError(self.env._("Receiver responses must be retained."))

    @api.model
    def _import_xml(self, content, filename, channel="email"):
        """Verify a RespuestaDTE or EnvioRecibos and link it to our issued DTEs.

        An invalid signature raises and nothing is stored. A verified file
        that refers to no DTE of the company is kept and flagged unmatched.
        """
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(
                self.env._("Only a DTE manager can record receiver responses.")
            )
        company = self.env.company
        digest = hashlib.sha256(content or b"").hexdigest()
        existing = self.search(
            [("company_id", "=", company.id), ("sha256", "=", digest)], limit=1
        )
        if existing:
            return existing
        signer = SignatureService()
        signer.env = self.env
        parsed = parse_receiver_response(content, signer)
        matches, problems = self._match_items(company, parsed)
        if not matches:
            match_state = "unmatched"
        else:
            match_state = "partial" if problems else "matched"
        response = super(DteReceiverResponse, self.sudo()).create(
            [
                {
                    "name": (filename or "response.xml")[:256],
                    "company_id": company.id,
                    "kind": parsed["kind"],
                    "channel": channel,
                    "content": base64.b64encode(content),
                    "sha256": digest,
                    "byte_count": len(content),
                    "responder_vat": parsed["responder_vat"][:32],
                    "signed_at": parsed["signed_at"][:64],
                    "match_state": match_state,
                    "diagnostics": "\n".join(problems)[:4000] or False,
                }
            ]
        )
        for document, item in matches:
            values = self._event_values(item, fallback_key=f"sha256:{digest[:16]}")
            document._store_receiver_events("email", [values], response=response)
        return response.with_env(self.env)

    @api.model
    def _event_values(self, item, fallback_key):
        return {
            "code": item["code"],
            "description": item["description"],
            "date_key": item["date_key"] or fallback_key,
            "date": chile_datetime_to_utc(item["date_key"]),
            "actor_vat": item["actor_vat"],
        }

    @api.model
    def _match_items(self, company, parsed):
        """Return ``(document, item)`` pairs for our DTEs and the other problems."""
        own_vat = normalize_vat(company.vat or "")
        problems = []
        if not own_vat or normalize_vat(parsed["recipient_vat"]) != own_vat:
            problems.append(
                self.env._(
                    "The response is addressed to another RUT: %s",
                    parsed["recipient_vat"] or "?",
                )
            )
            return [], problems
        if not parsed["items"]:
            problems.append(self.env._("The response refers to no DTE."))
        matches = []
        documents = self.env["l10n_cl.electronic.document"].sudo()
        for item in parsed["items"]:
            label = f"{item['issuer_vat']}/{item['document_code']}/{item['folio']}"
            document = documents.browse()
            if normalize_vat(item["issuer_vat"]) == own_vat and item["folio"].isdigit():
                document = documents.search(
                    [
                        ("company_id", "=", company.id),
                        ("document_code", "=", item["document_code"]),
                        ("folio", "=", int(item["folio"])),
                        ("preparation_state", "=", "queued"),
                    ],
                    limit=1,
                )
            if not document:
                problems.append(self.env._("No issued DTE matches %s.", label))
                continue
            matches.append((document, item))
        return matches, problems
