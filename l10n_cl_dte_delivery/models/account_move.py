# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import uuid

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.l10n_cl_dte_gateway.services.envelope_composer import (
    SiiEnvelopeComposer,
)

TEMPLATE = "l10n_cl_dte_delivery.mail_template_dte_delivery"


class AccountMove(models.Model):
    _inherit = "account.move"

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
        """Email the accepted DTE XML and the stamped PDF to the customer."""
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_user"):
            raise AccessError(_("Only a DTE user can deliver electronic documents."))
        for move in self:
            move._dte_deliver(raise_on_error=True)
        return True

    def _job_deliver_dte(self):
        """Deliver after SII acceptance; failures stay visible on the invoice."""
        for move in self:
            if move.dte_delivery_state == "not_sent":
                move._dte_deliver(raise_on_error=False)
        return True

    def _dte_delivery_document(self):
        self.ensure_one()
        documents = self.sudo().dte_document_ids.filtered(
            lambda document: document.fiscal_state == "accepted"
        )
        return documents.sorted("id")[-1:]

    def _dte_delivery_recipient(self):
        self.ensure_one()
        partner = self.partner_id.commercial_partner_id
        return partner.l10n_cl_dte_email or self.partner_id.email or partner.email

    def _dte_delivery_xml(self, document):
        """Return the receiver's EnvioDTE, or the signed DTE outside SII modes."""
        artifact = document._get_signed_artifact()
        artifact.action_verify_integrity()
        signed = base64.b64decode(
            artifact.with_context(bin_size=False).content, validate=True
        )
        company = self.company_id.sudo()
        if document.sii_environment not in {"certification", "production"}:
            return signed
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

    def _dte_deliver(self, raise_on_error):
        self.ensure_one()
        try:
            document = self._dte_delivery_document()
            if not document:
                raise UserError(_("Only a DTE accepted by SII can be delivered."))
            recipient = self._dte_delivery_recipient()
            if not recipient:
                raise UserError(
                    _("Set the customer's DTE exchange email or email address.")
                )
            attachments = self._dte_delivery_attachments(document)
        except UserError as error:
            if raise_on_error:
                raise
            self.sudo().write(
                {"dte_delivery_state": "failed", "dte_delivery_message": str(error)}
            )
            return False
        self.env.ref(TEMPLATE).sudo().send_mail(
            self.id,
            email_values={"email_to": recipient, "attachment_ids": attachments.ids},
        )
        self.sudo().write(
            {
                "dte_delivery_state": "sent",
                "dte_delivery_message": _("Sent to %(email)s", email=recipient),
            }
        )
        return True

    def _dte_delivery_attachments(self, document):
        name = f"DTE_{document.document_code}_{document.folio}"
        pdf, _type = (
            self.env["ir.actions.report"]
            .sudo()
            ._render_qweb_pdf("account.account_invoices", self.ids)
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
