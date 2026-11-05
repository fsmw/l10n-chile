# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo import _, fields, models
from odoo.exceptions import UserError


class IncomingDteImportWizard(models.TransientModel):
    _name = "l10n_cl.incoming.dte.import.wizard"
    _description = "Import Incoming DTE"

    filename = fields.Char(required=True)
    xml_file = fields.Binary(required=True, attachment=False)
    channel = fields.Selection(
        [
            ("sii_portal", "SII Portal"),
            ("email", "Email"),
            ("operator_imported", "Operator Import"),
        ],
        required=True,
        default="operator_imported",
    )

    def action_import(self):
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise UserError(_("Only a DTE manager can import incoming DTEs."))
        content = base64.b64decode(self.xml_file, validate=True)
        document = self.env["l10n_cl.incoming.dte"]._import_xml(
            content, self.channel, self.filename
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Incoming DTE"),
            "res_model": document._name,
            "res_id": document.id,
            "view_mode": "form",
            "target": "current",
        }


class IncomingDteResponseWizard(models.TransientModel):
    _name = "l10n_cl.incoming.dte.response.wizard"
    _description = "Prepare Incoming DTE Response"

    incoming_document_id = fields.Many2one(
        "l10n_cl.incoming.dte", required=True, readonly=True
    )
    response_code = fields.Char(required=True)
    response_text = fields.Char(required=True)
    contract_reference = fields.Char(
        required=True,
        help=(
            "Identify the current official SII technical contract used for "
            "this response."
        ),
    )

    def action_prepare(self):
        self.ensure_one()
        response = self.env["l10n_cl.incoming.dte.response"]._create_from_document(
            self.incoming_document_id,
            self.response_code,
            self.response_text,
            self.contract_reference,
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Incoming DTE Response"),
            "res_model": response._name,
            "res_id": response.id,
            "view_mode": "form",
            "target": "current",
        }
