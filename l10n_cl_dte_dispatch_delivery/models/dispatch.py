# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import uuid

import psycopg2

from odoo import fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.l10n_cl_dte_gateway.services.envelope_composer import (
    SiiEnvelopeComposer,
)

TEMPLATE = "l10n_cl_dte_dispatch_delivery.mail_template_dispatch_delivery"
GUIDE_REPORT = "l10n_cl_dte_dispatch_report.action_dispatch_guide"


class DteDispatch(models.Model):
    _inherit = "l10n_cl.dte.dispatch"

    dte_delivery_state = fields.Selection(
        [("not_sent", "Not Sent"), ("sent", "Sent"), ("failed", "Failed")],
        string="DTE Delivery",
        default="not_sent",
        required=True,
        readonly=True,
        copy=False,
    )
    dte_delivery_message = fields.Char(readonly=True, copy=False)

    def action_send_dte_to_customer(self):
        """Email the accepted guide XML and its PDF to the customer."""
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_user"):
            raise AccessError(
                self.env._("Only a DTE user can deliver electronic documents.")
            )
        for dispatch in self:
            dispatch._dte_deliver(raise_on_error=True)
        return True

    def _job_deliver_dte(self, document_id=None):
        """Deliver after SII acceptance; failures stay visible on the guide."""
        for dispatch in self:
            if dispatch.dte_delivery_state == "not_sent":
                dispatch._dte_deliver(raise_on_error=False, document_id=document_id)
        return True

    def _dte_delivery_document(self, document_id=None):
        self.ensure_one()
        documents = self.sudo().document_ids.filtered(
            lambda document: document.fiscal_state == "accepted"
            and (not document_id or document.id == document_id)
        )
        return documents.sorted("id")[-1:]

    def _dte_delivery_recipient(self):
        self.ensure_one()
        partner = self.partner_id.commercial_partner_id
        return partner.l10n_cl_dte_email or self.partner_id.email or partner.email

    def _dte_delivery_xml(self, document):
        """The receiver's EnvioDTE, or the signed DTE outside SII modes."""
        artifact = document._get_signed_artifact()
        artifact.action_verify_integrity()
        signed = base64.b64decode(
            artifact.with_context(bin_size=False).content, validate=True
        )
        if document.sii_environment not in {"certification", "production"}:
            return signed
        company = self.company_id.sudo()
        return SiiEnvelopeComposer().compose(
            company,
            [signed],
            {
                "id": "SetDTE-" + uuid.uuid4().hex,
                "sender_vat": company.dte_sii_sender_vat,
                "resolution_number": company.dte_sii_resolution_number,
                "resolution_date": company.dte_sii_resolution_date,
            },
            environment=document.sii_environment,
            receiver_vat=self.partner_id.commercial_partner_id.vat,
        )

    def _dte_delivery_attachments(self, document):
        name = f"DTE_{document.document_code}_{document.folio}"
        pdf, _type = (
            self.env["ir.actions.report"]
            .sudo()
            ._render_qweb_pdf(GUIDE_REPORT, self.ids)
        )
        values = [
            (f"{name}.xml", self._dte_delivery_xml(document), "application/xml"),
            (f"{name}.pdf", pdf, "application/pdf"),
        ]
        return (
            self.env["ir.attachment"]
            .sudo()
            .create(
                [
                    {
                        "name": filename,
                        "raw": content,
                        "mimetype": mimetype,
                        "res_model": self._name,
                        "res_id": self.id,
                    }
                    for filename, content, mimetype in values
                ]
            )
        )

    def _dte_deliver(self, raise_on_error, document_id=None):
        self.ensure_one()
        try:
            document = self._dte_delivery_document(document_id)
            if not document:
                raise UserError(
                    self.env._("Only a guide accepted by SII can be delivered.")
                )
            recipient = self._dte_delivery_recipient()
            if not recipient:
                raise UserError(
                    self.env._("Set the customer's DTE exchange email or email.")
                )
            attachments = self._dte_delivery_attachments(document)
        except Exception as error:
            if raise_on_error or isinstance(error, psycopg2.Error):
                raise
            self.sudo().write(
                {
                    "dte_delivery_state": "failed",
                    "dte_delivery_message": str(error)[:250] or type(error).__name__,
                }
            )
            return False
        self.env.ref(TEMPLATE).sudo().send_mail(
            self.id,
            email_values={"email_to": recipient, "attachment_ids": attachments.ids},
        )
        self.sudo().write(
            {
                "dte_delivery_state": "sent",
                "dte_delivery_message": self.env._(
                    "Queued for %(email)s", email=recipient
                ),
            }
        )
        return True
