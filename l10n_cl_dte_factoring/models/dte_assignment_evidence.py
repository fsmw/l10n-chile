# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import binascii
import hashlib

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError


class DteAssignmentEvidence(models.Model):
    _name = "l10n_cl.dte.assignment.evidence"
    _description = "Immutable DTE Assignment Evidence"
    _order = "id desc"
    _check_company_auto = True

    assignment_id = fields.Many2one(
        "l10n_cl.dte.assignment", required=True, index=True, ondelete="restrict"
    )
    company_id = fields.Many2one(
        related="assignment_id.company_id", store=True, index=True
    )
    role = fields.Selection(
        [
            ("aec_xml", "Signed AEC"),
            ("upload_response", "RTC Upload Response"),
            ("status_response", "RTC Status Response"),
            ("reconcile_response", "RTC Assignment Query Response"),
        ],
        required=True,
        index=True,
    )
    content = fields.Binary(
        required=True,
        attachment=True,
        copy=False,
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    file_name = fields.Char()
    sha256 = fields.Char(required=True, index=True, copy=False, readonly=True)
    byte_count = fields.Integer(readonly=True, copy=False)
    mimetype = fields.Char(required=True, default="application/xml")
    created_at = fields.Datetime(default=fields.Datetime.now, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        """Record the digest and size of the exact persisted bytes."""
        if not self.env.su and not self.env.user.has_group(
            "l10n_cl_dte_core.group_dte_manager"
        ):
            raise AccessError(self.env._("Only a DTE manager can add evidence."))
        normalized = []
        for vals in vals_list:
            values = dict(vals)
            raw = self._decode(values.get("content"))
            values["sha256"] = hashlib.sha256(raw).hexdigest()
            values["byte_count"] = len(raw)
            normalized.append(values)
        return super().create(normalized)

    def _decode(self, encoded):
        if not encoded:
            raise ValidationError(self.env._("Evidence content is required."))
        try:
            return base64.b64decode(encoded, validate=True)
        except (binascii.Error, TypeError, ValueError) as error:
            raise ValidationError(
                self.env._("Evidence must be valid base64 content.")
            ) from error

    def _raw_content(self):
        """Return the stored bytes after proving they match their digest."""
        self.ensure_one()
        raw = self._decode(self.sudo().with_context(bin_size=False).content)
        if hashlib.sha256(raw).hexdigest() != self.sha256 or len(raw) != (
            self.byte_count
        ):
            raise ValidationError(
                self.env._("Stored evidence does not match its recorded digest.")
            )
        return raw

    def action_verify_integrity(self):
        """Recompute the digest of the stored content (DTE managers only)."""
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(
                self.env._("Only a DTE manager can verify evidence content.")
            )
        for evidence in self:
            evidence._raw_content()
        return True

    def write(self, vals):  # pylint: disable=method-required-super
        raise AccessError(self.env._("Assignment evidence is immutable."))

    def unlink(self):  # pylint: disable=method-required-super
        raise AccessError(self.env._("Assignment evidence cannot be deleted."))

    def copy(self, default=None):  # pylint: disable=method-required-super
        raise AccessError(self.env._("Assignment evidence cannot be duplicated."))
