# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import SQL

_PREPARATION_TRANSITIONS = {
    "draft": {"authorized"},
    "authorized": {"numbered"},
    "numbered": {"prepared"},
    "prepared": {"signed"},
    "signed": {"queued"},
    "queued": set(),
}
_FISCAL_TRANSITIONS = {
    "unknown": {"submitted", "outcome_unknown"},
    "submitted": {"submitted", "processing", "accepted", "rejected", "outcome_unknown"},
    "processing": {"processing", "accepted", "rejected", "outcome_unknown"},
    "outcome_unknown": {
        "submitted",
        "processing",
        "accepted",
        "rejected",
        "outcome_unknown",
    },
    "accepted": {"accepted"},
    "rejected": {"rejected"},
}
_EVENT_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ElectronicDocument(models.Model):
    _name = "l10n_cl.electronic.document"
    _description = "Chilean Electronic Tax Document"
    _order = "id desc"
    _check_company_auto = True
    _sql_constraints = [
        (
            "source_key_company_uniq",
            "unique(company_id, source_key)",
            "A source invoice can only have one electronic document per company.",
        )
    ]

    name = fields.Char(required=True, index=True, copy=False)
    company_id = fields.Many2one(
        "res.company", required=True, index=True, default=lambda self: self.env.company
    )
    document_code = fields.Char(required=True, index=True)
    folio = fields.Integer(readonly=True, index=True, copy=False)
    preparation_state = fields.Selection(
        [
            ("draft", "Draft"),
            ("authorized", "CAF Authorized"),
            ("numbered", "Folio Reserved"),
            ("prepared", "XML Prepared"),
            ("signed", "Signed"),
            ("queued", "Queued"),
        ],
        required=True,
        default="draft",
        index=True,
        copy=False,
    )
    fiscal_state = fields.Selection(
        [
            ("unknown", "Unknown"),
            ("submitted", "Submitted"),
            ("processing", "Processing"),
            ("accepted", "Accepted"),
            ("rejected", "Rejected"),
            ("outcome_unknown", "Outcome Unknown"),
        ],
        required=True,
        default="unknown",
        index=True,
        copy=False,
    )
    source_key = fields.Char(required=True, index=True, copy=False)
    payload_digest = fields.Char(readonly=True, index=True, copy=False)
    artifact_ids = fields.One2many(
        "l10n_cl.electronic.artifact", "document_id", string="Evidence"
    )
    event_ids = fields.One2many("l10n_cl.electronic.event", "document_id")
    active = fields.Boolean(default=True)

    def init(self):
        """Index reserved folios while ignoring the unnumbered zero value."""
        self.env.cr.execute(
            SQL(
                "CREATE UNIQUE INDEX IF NOT EXISTS %s ON %s "
                "(company_id, document_code, folio) WHERE folio > 0",
                SQL.identifier("l10n_cl_electronic_document_folio_uniq"),
                SQL.identifier(self._table),
            )
        )

    @api.model_create_multi
    def create(self, vals_list):
        """Create a new document only in its initial, unnumbered state."""
        initial_values = {
            "preparation_state": "draft",
            "fiscal_state": "unknown",
            "folio": False,
            "payload_digest": False,
        }
        normalized_vals_list = []
        for vals in vals_list:
            vals = vals.copy()
            if vals.get("source_key"):
                vals["source_key"] = vals["source_key"].strip()
            if vals.get("document_code"):
                vals["document_code"] = vals["document_code"].strip()
            for field_name, initial_value in initial_values.items():
                if field_name in vals and vals[field_name] not in (
                    False,
                    initial_value,
                ):
                    raise ValidationError(
                        _("A DTE must be created in its initial unnumbered state.")
                    )
                vals[field_name] = initial_value
            normalized_vals_list.append(vals)
        documents = super().create(normalized_vals_list)
        documents._create_audit_events(
            [
                {
                    "event_code": "document_created",
                    "state_domain": "preparation",
                    "to_state": "draft",
                }
            ]
            * len(documents)
        )
        return documents

    def write(self, vals):
        """Keep identity and lifecycle fields behind validated actions."""
        protected_fields = {
            "name",
            "company_id",
            "document_code",
            "folio",
            "preparation_state",
            "fiscal_state",
            "source_key",
            "payload_digest",
        }
        if protected_fields.intersection(vals):
            raise UserError(
                _(
                    "DTE identity and state fields can only change through "
                    "validated actions."
                )
            )
        return super().write(vals)

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("Electronic tax documents cannot be deleted."))

    def copy(self, default=None):  # pylint: disable=method-required-super
        raise UserError(_("Electronic tax documents cannot be duplicated."))

    @api.constrains("document_code", "source_key")
    def _check_document_identity(self):
        for document in self:
            if not (document.document_code or "").isdigit():
                raise ValidationError(_("The DTE type code must contain digits only."))
            if not (document.source_key or "").strip():
                raise ValidationError(_("The source key cannot be empty."))

    @api.constrains("folio", "preparation_state")
    def _check_folio_state(self):
        numbered_states = {"numbered", "prepared", "signed", "queued"}
        for document in self:
            if document.folio < 0:
                raise ValidationError(_("A DTE folio cannot be negative."))
            if document.preparation_state in numbered_states and document.folio <= 0:
                raise ValidationError(
                    _("A positive folio is required after the numbered state.")
                )
            if document.preparation_state not in numbered_states and document.folio:
                raise ValidationError(
                    _("A folio cannot be assigned before the numbered state.")
                )

    def action_record_transition(
        self,
        target_state,
        event_code,
        message=None,
        correlation_key=None,
        artifact_id=False,
        folio_number=False,
    ):
        """Apply one valid lifecycle transition and persist its audit event."""
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(_("Only a DTE manager can change document state."))
        if not isinstance(event_code, str) or not _EVENT_CODE_RE.fullmatch(event_code):
            raise ValidationError(
                _("Event codes must use lowercase letters, digits and underscores.")
            )
        if message and (not isinstance(message, str) or len(message) > 4000):
            raise ValidationError(
                _("The event message cannot exceed 4,000 characters.")
            )
        if correlation_key and (
            not isinstance(correlation_key, str) or len(correlation_key) > 256
        ):
            raise ValidationError(
                _("The correlation key cannot exceed 256 characters.")
            )
        artifact = self.env["l10n_cl.electronic.artifact"].browse(artifact_id).exists()
        if artifact_id and (len(artifact) != 1 or artifact.document_id != self):
            raise ValidationError(
                _("The audit artifact must belong to the electronic document.")
            )

        if not isinstance(target_state, str):
            raise ValidationError(_("A valid DTE state is required."))
        if target_state in _PREPARATION_TRANSITIONS:
            state_domain = "preparation"
            current_state = self.preparation_state
            transitions = _PREPARATION_TRANSITIONS
            field_name = "preparation_state"
        elif target_state in _FISCAL_TRANSITIONS:
            state_domain = "fiscal"
            current_state = self.fiscal_state
            transitions = _FISCAL_TRANSITIONS
            field_name = "fiscal_state"
        else:
            raise ValidationError(_("Unsupported DTE state: %s") % target_state)

        if target_state not in transitions[current_state]:
            raise ValidationError(
                _("Transition from %(source)s to %(target)s is not allowed.")
                % {"source": current_state, "target": target_state}
            )
        write_values = {field_name: target_state}
        if target_state == "numbered":
            if (
                not isinstance(folio_number, int)
                or isinstance(folio_number, bool)
                or folio_number <= 0
            ):
                raise ValidationError(
                    _("A positive folio number is required for the numbered state.")
                )
            write_values["folio"] = folio_number
        elif folio_number not in (False, None):
            raise ValidationError(
                _("A folio number can only be assigned with the numbered state.")
            )
        if (
            state_domain == "preparation"
            and target_state
            in {
                "prepared",
                "signed",
                "queued",
            }
            and not self.folio
        ):
            raise ValidationError(_("A folio must be reserved before preparing XML."))
        if state_domain == "fiscal" and self.preparation_state != "queued":
            raise ValidationError(
                _("SII status cannot be recorded before the signed DTE is queued.")
            )

        previous_state = current_state
        super().write(write_values)
        self._create_audit_events(
            [
                {
                    "event_code": event_code,
                    "state_domain": state_domain,
                    "from_state": previous_state,
                    "to_state": target_state,
                    "message": message or False,
                    "correlation_key": correlation_key or False,
                    "artifact_id": artifact.id if artifact else False,
                }
            ]
        )
        return True

    def _create_audit_events(self, event_values_list):
        """Create immutable audit events through a narrowly scoped elevation."""
        event_model = self.env["l10n_cl.electronic.event"].sudo()
        actor_id = self.env.uid
        for document, event_values in zip(self, event_values_list, strict=True):
            event_model._create_from_document(
                {
                    **event_values,
                    "document_id": document.id,
                    "actor_id": actor_id,
                }
            )
