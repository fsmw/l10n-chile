# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from cryptography import x509
from cryptography.x509.oid import NameOID

from odoo import api, fields, models


class Certificate(models.Model):
    _inherit = "certificate.certificate"

    user_id = fields.Many2one(
        "res.users",
        string="Certificate owner",
        help="When set, only this user can issue DTE with this certificate.",
    )
    subject_serial_number = fields.Char(
        compute="_compute_subject_serial_number",
        store=True,
        readonly=False,
        copy=False,
    )
    last_token = fields.Char(copy=False, groups="base.group_system")
    last_rest_token = fields.Char(copy=False, groups="base.group_system")
    token_time = fields.Datetime(copy=False, groups="base.group_system")

    @api.depends("pem_certificate")
    def _compute_subject_serial_number(self):
        for certificate in self:
            if certificate.subject_serial_number or not certificate.pem_certificate:
                continue
            pem = base64.b64decode(
                certificate.with_context(bin_size=False).pem_certificate
            )
            cert = x509.load_pem_x509_certificate(pem)
            attributes = cert.subject.get_attributes_for_oid(NameOID.SERIAL_NUMBER)
            if attributes:
                certificate.subject_serial_number = attributes[0].value
