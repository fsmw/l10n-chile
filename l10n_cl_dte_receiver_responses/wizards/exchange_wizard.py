# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.incoming_exchange import EXCHANGE_KINDS


class IncomingDteExchangeWizard(models.TransientModel):
    _name = "l10n_cl.incoming.dte.exchange.wizard"
    _description = "Email Incoming DTE Exchange Response"

    kind = fields.Selection(
        [item for item in EXCHANGE_KINDS if item[0] != "reception"],
        required=True,
        default="result",
    )
    document_ids = fields.Many2many(
        "l10n_cl.incoming.dte", string="Incoming DTEs", required=True
    )
    result_state = fields.Selection(
        [
            ("0", "Accepted"),
            ("1", "Accepted with Objections"),
            ("2", "Rejected"),
        ],
        string="Commercial Result",
        default="0",
    )
    gloss = fields.Char(
        string="Reason",
        size=256,
        help="Text of EstadoDTEGlosa. The standard text of the result is used "
        "when it is empty.",
    )
    site = fields.Char(
        string="Receipt Place",
        size=80,
        help="Place (Recinto) where the goods or services were received.",
    )
    send_now = fields.Boolean(default=True, help="Email the signed response now.")
    registry_response_id = fields.Many2one("l10n_cl.incoming.dte.response")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if (
            "document_ids" in fields_list
            and not values.get("document_ids")
            and self.env.context.get("active_model") == "l10n_cl.incoming.dte"
        ):
            values["document_ids"] = [
                fields.Command.set(self.env.context.get("active_ids", []))
            ]
        if "site" in fields_list and not values.get("site"):
            exchanges = self.env["l10n_cl.incoming.dte.exchange"]
            values["site"] = exchanges._default_site(self.env.company)
        return values

    def action_confirm(self):
        """Sign one response per supplier and email it when requested."""
        self.ensure_one()
        exchanges = self.env["l10n_cl.incoming.dte.exchange"]
        if not self.document_ids:
            raise UserError(_("Select the incoming DTEs to answer."))
        groups = {}
        for document in self.document_ids:
            key = (document.company_id.id, document.issuer_vat.replace(".", ""))
            groups.setdefault(key, self.document_ids.browse())
            groups[key] |= document
        prepared = exchanges.browse()
        for documents in groups.values():
            prepared |= exchanges._prepare_documents(
                self.kind,
                documents,
                state=self.result_state or "0",
                gloss=self.gloss,
                site=self.site,
                registry_response=self.registry_response_id,
            )
        if self.send_now:
            prepared._email_pending()
        return prepared.action_open()
