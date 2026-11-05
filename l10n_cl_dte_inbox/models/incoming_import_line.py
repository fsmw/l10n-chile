# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .incoming_import import CHECK_STATES


class IncomingDteImportLine(models.Model):
    _name = "l10n_cl.incoming.dte.import.line"
    _description = "Incoming DTE Import Result"
    _order = "import_id, sequence, id"
    _check_company_auto = True

    import_id = fields.Many2one(
        "l10n_cl.incoming.dte.import",
        required=True,
        readonly=True,
        ondelete="restrict",
        check_company=True,
    )
    company_id = fields.Many2one(related="import_id.company_id", store=True, index=True)
    sequence = fields.Integer(required=True, readonly=True)
    incoming_document_id = fields.Many2one(
        "l10n_cl.incoming.dte", readonly=True, check_company=True, ondelete="restrict"
    )
    issuer_vat = fields.Char(readonly=True)
    document_code = fields.Char(readonly=True)
    folio = fields.Integer(readonly=True)
    schema_state = fields.Selection(CHECK_STATES, required=True, readonly=True)
    schema_name = fields.Char(readonly=True)
    signature_state = fields.Selection(CHECK_STATES, required=True, readonly=True)
    ted_state = fields.Selection(CHECK_STATES, required=True, readonly=True)
    result = fields.Selection(
        [
            ("imported", "Imported"),
            ("duplicate", "Duplicate"),
            ("invalid", "Invalid"),
            ("blocked", "Blocked by File Errors"),
        ],
        required=True,
        readonly=True,
    )
    diagnostics = fields.Text(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super  # noqa: ARG002
        raise UserError(_("Import results are created through the XML import action."))

    @api.model
    def _create_internal(self, values):
        return super(IncomingDteImportLine, self.sudo()).create(values)

    def write(self, values):  # pylint: disable=method-required-super  # noqa: ARG002
        raise UserError(_("Incoming import results are immutable."))

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("Incoming import results must be retained."))
