# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
import json

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from psycopg2.errors import SerializationFailure, UniqueViolation

from ..services.incoming_validation import IncomingValidationService, normalize_vat

CHECK_STATES = [
    ("not_checked", "Not Checked"),
    ("valid", "Valid"),
    ("invalid", "Invalid"),
    ("unsupported", "Unsupported"),
]
ENVIRONMENTS = [
    ("demo", "Demo"),
    ("certification", "Certification"),
    ("production", "Production"),
]


class IncomingDteImport(models.Model):
    _name = "l10n_cl.incoming.dte.import"
    _description = "Incoming DTE Import Evidence"
    _order = "id desc"
    _check_company_auto = True

    name = fields.Char(required=True, readonly=True)
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, index=True, ondelete="restrict"
    )
    environment = fields.Selection(
        ENVIRONMENTS, required=True, readonly=True, index=True
    )
    channel = fields.Selection(
        [
            ("sii_portal", "SII Portal"),
            ("email", "Email"),
            ("operator_imported", "Operator Import"),
        ],
        required=True,
        readonly=True,
    )
    imported_at = fields.Datetime(
        required=True, readonly=True, default=fields.Datetime.now
    )
    actor_id = fields.Many2one(
        "res.users", required=True, readonly=True, ondelete="restrict"
    )
    raw_xml = fields.Binary(
        required=True,
        readonly=True,
        attachment=True,
        copy=False,
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    filename = fields.Char(readonly=True)
    xml_sha256 = fields.Char(required=True, readonly=True, index=True)
    state = fields.Selection(
        [("imported", "Imported"), ("invalid", "Invalid")],
        required=True,
        readonly=True,
        index=True,
    )
    schema_state = fields.Selection(CHECK_STATES, required=True, readonly=True)
    schema_name = fields.Char(readonly=True)
    signature_state = fields.Selection(CHECK_STATES, required=True, readonly=True)
    diagnostics = fields.Text(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    line_ids = fields.One2many(
        "l10n_cl.incoming.dte.import.line", "import_id", readonly=True
    )
    document_count = fields.Integer(readonly=True)
    imported_count = fields.Integer(readonly=True)
    duplicate_count = fields.Integer(readonly=True)

    _sql_constraints = [
        (
            "incoming_import_digest_uniq",
            "unique(company_id, environment, xml_sha256)",
            "This incoming XML file is already registered.",
        )
    ]

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super  # noqa: ARG002
        raise UserError(_("Use the incoming XML import action."))

    @api.model
    def _import_xml(self, content, channel="operator_imported", filename=False):
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(_("Only a DTE manager can import incoming DTEs."))
        if channel not in dict(self._fields["channel"].selection):
            raise ValidationError(_("The incoming DTE source channel is invalid."))
        if not isinstance(content, bytes) or not content:
            raise ValidationError(_("The incoming DTE XML is empty."))
        if len(content) > 20 * 1024 * 1024:
            raise ValidationError(_("The incoming XML exceeds 20 MiB."))
        digest = hashlib.sha256(content).hexdigest()
        company = self.env.company
        environment = company.dte_sii_environment
        existing = self.search(
            [
                ("company_id", "=", company.id),
                ("environment", "=", environment),
                ("xml_sha256", "=", digest),
            ],
            limit=1,
        )
        if existing:
            return existing
        result = IncomingValidationService(self.env).inspect(content)
        planned = self._plan_documents(result)
        invalid = bool(result["diagnostics"]) or any(
            item["diagnostics"] for item in result["documents"]
        )
        # A race on file/identity uniqueness must retry the whole RPC with a fresh
        # PostgreSQL snapshot. Never leave a partially registered envelope.
        try:
            with self.env.cr.savepoint():
                return self._store_import(
                    content, channel, filename, digest, result, planned, invalid
                )
        except UniqueViolation as error:
            raise SerializationFailure(
                "Retry concurrent incoming DTE import"
            ) from error

    @api.model
    def _plan_documents(self, result):
        planned = []
        seen = set()
        expected_receiver = normalize_vat(self.env.company.partner_id.vat or "")
        for item in result["documents"]:
            identity = item.get("identity", {})
            if identity:
                key = (
                    normalize_vat(identity["issuer_vat"]),
                    identity["document_code"],
                    identity["folio"],
                )
                if key in seen:
                    item["diagnostics"].append(
                        {
                            "stage": "identity",
                            "message": _(
                                "The same DTE identity occurs more than once "
                                "in this file."
                            ),
                        }
                    )
                seen.add(key)
                if (
                    not expected_receiver
                    or normalize_vat(identity["receiver_vat"]) != expected_receiver
                ):
                    item["diagnostics"].append(
                        {
                            "stage": "identity",
                            "message": _(
                                "The DTE receiver does not match the current company."
                            ),
                        }
                    )
                existing = (
                    self.env["l10n_cl.incoming.dte"]
                    .search(
                        [
                            ("company_id", "=", self.env.company.id),
                            (
                                "sii_environment",
                                "=",
                                self.env.company.dte_sii_environment,
                            ),
                            ("document_code", "=", key[1]),
                            ("folio", "=", key[2]),
                        ]
                    )
                    .filtered(
                        lambda record, issuer=key[0]: normalize_vat(record.issuer_vat)
                        == issuer
                    )
                )
                try:
                    conflict = existing and (
                        len(existing) != 1
                        or existing._identity_digest() != item["identity_digest"]
                    )
                except ValidationError:
                    conflict = True
                if conflict:
                    item["diagnostics"].append(
                        {
                            "stage": "conflict",
                            "message": _(
                                "This DTE identity already exists with "
                                "different signed XML."
                            ),
                        }
                    )
            else:
                existing = self.env["l10n_cl.incoming.dte"]
            planned.append(existing)
        return planned

    @api.model
    def _store_import(
        self, content, channel, filename, digest, result, planned, invalid
    ):
        batch = (
            super(IncomingDteImport, self.sudo())
            .create(
                {
                    "name": filename or _("Incoming XML"),
                    "company_id": self.env.company.id,
                    "environment": self.env.company.dte_sii_environment,
                    "channel": channel,
                    "actor_id": self.env.uid,
                    "raw_xml": base64.b64encode(content),
                    "filename": filename or "incoming.xml",
                    "xml_sha256": digest,
                    "state": "invalid" if invalid else "imported",
                    "schema_state": result["schema_state"],
                    "schema_name": result["schema_name"],
                    "signature_state": result["signature_state"],
                    "diagnostics": json.dumps(
                        result["diagnostics"], ensure_ascii=False, indent=2
                    ),
                    "document_count": len(result["documents"]),
                    "imported_count": 0
                    if invalid
                    else sum(not record for record in planned),
                    "duplicate_count": 0
                    if invalid
                    else sum(bool(record) for record in planned),
                }
            )
            .with_env(self.env)
        )
        for item, existing in zip(result["documents"], planned, strict=True):
            identity = item.get("identity", {})
            document = self.env["l10n_cl.incoming.dte"]
            if not invalid:
                document = existing or document._import_xml(
                    content if result.get("kind") == "DTE" else item["xml"],
                    channel,
                    filename,
                    validation=item,
                )
            values = {
                "import_id": batch.id,
                "sequence": item["position"],
                "incoming_document_id": document.id,
                "issuer_vat": identity.get("issuer_vat"),
                "document_code": identity.get("document_code"),
                "folio": identity.get("folio"),
                "schema_state": item["schema_state"],
                "schema_name": item["schema_name"],
                "signature_state": item["signature_state"],
                "ted_state": item["ted_state"],
                "result": ("invalid" if item["diagnostics"] else "blocked")
                if invalid
                else ("duplicate" if existing else "imported"),
                "diagnostics": json.dumps(
                    item["diagnostics"], ensure_ascii=False, indent=2
                ),
            }
            self.env["l10n_cl.incoming.dte.import.line"]._create_internal(values)
        return batch

    def write(self, values):  # pylint: disable=method-required-super  # noqa: ARG002
        raise UserError(_("Incoming import evidence is immutable."))

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("Incoming import evidence must be retained."))
