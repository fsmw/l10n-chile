# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
import json
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class DteSigningProfile(models.Model):
    _inherit = "l10n_cl.dte.signing.profile"

    sii_auth_verified_until = fields.Datetime(
        readonly=True, copy=False, groups="l10n_cl_dte_core.group_dte_manager"
    )
    sii_auth_identity_digest = fields.Char(
        readonly=True, copy=False, groups="l10n_cl_dte_core.group_dte_manager"
    )

    def _sii_auth_configuration_digest(self):
        self.ensure_one()
        certificate = self.sudo().certificate_id
        identity = {
            "certificate": hashlib.sha256(
                base64.b64decode(certificate.pem_certificate or b"")
            ).hexdigest(),
            "key_id": certificate.private_key_id.id,
            "issuer": self.company_id.vat,
            "sender": self.company_id.dte_sii_sender_vat,
            "environment": self.sii_environment,
        }
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def _record_sii_authentication(self, success):
        self.ensure_one()
        self._check_manager()
        return super().write(
            {
                "sii_auth_verified_until": fields.Datetime.now() + timedelta(minutes=30)
                if success
                else False,
                "sii_auth_identity_digest": self._sii_auth_configuration_digest()
                if success
                else False,
            }
        )

    @api.model_create_multi
    def create(self, values_list):
        for values in values_list:
            if {"sii_auth_verified_until", "sii_auth_identity_digest"}.intersection(
                values
            ):
                raise UserError(
                    _("SII authentication proof can only be recorded by the transport.")
                )
        return super().create(values_list)

    def write(self, values):
        if {"sii_auth_verified_until", "sii_auth_identity_digest"}.intersection(values):
            raise UserError(
                _("SII authentication proof can only be recorded by the transport.")
            )
        return super().write(values)
