# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import Command, api, fields, models

from ..services.rcv_draft_service import RcvDraftService


class RcvDraftWizard(models.TransientModel):
    """Review and create the vendor bill draft of an RCV purchase.

    With a received DTE in the inbox, the draft is prepared from its signed
    XML by the incoming DTE vendor bill preview; otherwise from the reviewed
    RCV amounts below.
    """

    _name = "l10n_cl.dte.rcv.draft.wizard"
    _description = "Prepare RCV Vendor Bill Draft"
    _check_company_auto = True

    document_id = fields.Many2one(
        "l10n_cl.dte.rcv.document", required=True, readonly=True, ondelete="cascade"
    )
    company_id = fields.Many2one(related="document_id.company_id")
    currency_id = fields.Many2one(related="document_id.currency_id")
    incoming_dte_id = fields.Many2one(related="document_id.incoming_dte_id")
    issuer_vat = fields.Char(related="document_id.issuer_vat")
    issuer_name = fields.Char(related="document_id.issuer_name")
    tax_period = fields.Char(related="document_id.tax_period")
    net_amount = fields.Monetary(related="document_id.net_amount")
    exempt_amount = fields.Monetary(related="document_id.exempt_amount")
    vat_amount = fields.Monetary(related="document_id.vat_amount")
    total_amount = fields.Monetary(related="document_id.total_amount")
    journal_id = fields.Many2one(
        "account.journal",
        check_company=True,
        domain="[('company_id', '=', company_id), ('type', '=', 'purchase'), "
        "('l10n_latam_use_documents', '=', True)]",
    )
    partner_candidate_ids = fields.Many2many(
        "res.partner", compute="_compute_partner_candidate_ids"
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Supplier",
        domain="[('id', 'in', partner_candidate_ids)]",
    )
    create_partner = fields.Boolean(
        string="Create the Supplier",
        help="No supplier has the RUT of the document: create it with this name.",
    )
    new_partner_name = fields.Char(string="New Supplier Name")
    line_ids = fields.One2many(
        "l10n_cl.dte.rcv.draft.wizard.line", "wizard_id", string="Lines"
    )

    @api.depends("document_id")
    def _compute_partner_candidate_ids(self):
        service = RcvDraftService(self.env)
        for wizard in self:
            wizard.partner_candidate_ids = (
                service.partner_candidates(wizard.document_id)
                if wizard.document_id
                else False
            )

    @api.model_create_multi
    def create(self, vals_list):
        wizards = super().create(vals_list)
        for wizard in wizards:
            wizard._prepare_defaults()
        return wizards

    def _prepare_defaults(self):
        """Propose the supplier, the journal and the lines from the RCV."""
        self.ensure_one()
        service = RcvDraftService(self.env)
        document = self.document_id
        candidates = service.partner_candidates(document)
        journal = self.journal_id or service.journal(document.company_id)
        values = {"journal_id": journal.id}
        partner = self.partner_id or (candidates if len(candidates) == 1 else False)
        if partner:
            values["partner_id"] = partner.id
        if not candidates and not self.new_partner_name:
            values["new_partner_name"] = document.issuer_name
        if not document.incoming_dte_id and not self.line_ids:
            account = service.default_account(
                partner or self.env["res.partner"], journal
            )
            values["line_ids"] = [
                Command.create(
                    {
                        "name": line["name"],
                        "price_unit": line["price_unit"],
                        "account_id": line["account_id"],
                        "tax_ids": [Command.set(line["tax_ids"])],
                        "component": line["component"],
                    }
                )
                for line in service.summary_lines(document, account)
            ]
        self.write(values)

    def _line_values(self):
        return [
            {
                "name": line.name,
                "price_unit": line.price_unit,
                "account_id": line.account_id.id,
                "tax_ids": line.tax_ids.ids,
                "component": line.component,
            }
            for line in self.line_ids
        ]

    def action_confirm(self):
        """Create the draft from the RCV, or open the preview of the DTE."""
        self.ensure_one()
        service = RcvDraftService(self.env)
        document = self.document_id
        service.check_allowed(document)
        service.lock(document)
        service.check_eligible(document)
        partner = service.resolve_partner(
            document, self.partner_id, self.create_partner, self.new_partner_name
        )
        journal = self.journal_id or service.journal(document.company_id)
        incoming = document._link_rcv_incoming_dte()
        if incoming:
            return self._open_xml_preview(
                service.xml_wizard(document, incoming, journal)
            )
        lines = self._line_values()
        service.check_lines(lines)
        move = service.create_summary_draft(document, partner, journal, lines)
        service.finish(
            document, move, self.env._("Draft prepared from the reviewed RCV amounts.")
        )
        return move.get_formview_action()

    def _open_xml_preview(self, wizard):
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Prepare Vendor Bill"),
            "res_model": wizard._name,
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }
