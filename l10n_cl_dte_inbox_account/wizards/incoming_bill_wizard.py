# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError

from ..services.binding_service import IncomingAccountBindingService
from ..services.common import reject
from ..services.policy_service import IncomingAccountPolicyService


class IncomingDteBillWizard(models.TransientModel):
    _name = "l10n_cl.incoming.dte.bill.wizard"
    _description = "Incoming DTE Vendor Bill Preview"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company",
        required=True,
        readonly=True,
        default=lambda self: self.env.company,
        ondelete="cascade",
    )
    incoming_document_id = fields.Many2one(
        "l10n_cl.incoming.dte",
        required=True,
        readonly=True,
        check_company=True,
        ondelete="cascade",
    )
    mode = fields.Selection(
        [("create", "Create Draft"), ("link", "Link Existing Draft")],
        required=True,
        default="create",
    )
    partner_id = fields.Many2one(
        "res.partner", readonly=True, check_company=True, ondelete="restrict"
    )
    journal_id = fields.Many2one(
        "account.journal",
        check_company=True,
        domain=(
            "[('company_id', '=', company_id), ('type', '=', 'purchase'), "
            "('l10n_latam_use_documents', '=', True)]"
        ),
    )
    currency_id = fields.Many2one(
        "res.currency",
        required=True,
        readonly=True,
        default=lambda self: self.env.company.currency_id,
    )
    document_code = fields.Char(readonly=True)
    folio = fields.Char(readonly=True)
    issue_date = fields.Date(readonly=True)
    due_date = fields.Date(readonly=True)
    accounting_date = fields.Date(required=True, default=fields.Date.context_today)
    source_net = fields.Monetary(currency_field="currency_id", readonly=True)
    source_exempt = fields.Monetary(currency_field="currency_id", readonly=True)
    source_vat = fields.Monetary(currency_field="currency_id", readonly=True)
    source_total = fields.Monetary(currency_field="currency_id", readonly=True)
    candidate_move_ids = fields.Many2many(
        "account.move", string="Existing Candidates", readonly=True, check_company=True
    )
    existing_move_id = fields.Many2one(
        "account.move",
        string="Existing Draft",
        check_company=True,
        domain=(
            "[('id', 'in', candidate_move_ids), ('state', '=', 'draft'), "
            "('move_type', '=', 'in_invoice')]"
        ),
    )
    line_ids = fields.One2many(
        "l10n_cl.incoming.dte.bill.wizard.line", "wizard_id", string="Fiscal Details"
    )
    diagnostic_text = fields.Text(readonly=True)
    has_blockers = fields.Boolean(readonly=True, default=True)
    preview_digest = fields.Char(readonly=True)

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        source_id = self.env.context.get("default_incoming_document_id")
        if source_id:
            incoming = self.env["l10n_cl.incoming.dte"].browse(source_id)
            values.update(
                self._preview_values(
                    IncomingAccountBindingService(self.env).prepare(incoming)
                )
            )
        return {key: value for key, value in values.items() if key in field_names}

    def action_refresh_preview(self):
        self._check_operator()
        preview = IncomingAccountBindingService(self.env).prepare(
            self.incoming_document_id,
            self._prepare_overrides(),
            self.journal_id,
            self.accounting_date,
            self.mode,
            self.existing_move_id,
        )
        self.write(self._preview_values(preview))
        return dict(
            type="ir.actions.act_window",
            name=self.env._("Prepare Vendor Bill"),
            res_model=self._name,
            res_id=self.id,
            view_mode="form",
            target="new",
        )

    def action_confirm(self):
        self._check_operator()
        move = IncomingAccountBindingService(self.env).confirm(self)
        return move.get_formview_action()

    def _prepare_overrides(self):
        self._check_operator()
        self.line_ids.check_access("read")
        ordinals = self.line_ids.mapped("sequence")
        if len(set(ordinals)) != len(ordinals):
            reject(self.env, "existing_bill_incompatible")
        return {
            line.sequence: dict(
                product_id=line.product_id.id,
                account_id=line.account_id.id,
                tax_id=line.tax_id.id,
                uom_id=line.uom_id.id,
                existing_line_id=line.existing_line_id.id,
            )
            for line in self.line_ids
        }

    def _check_operator(self):
        self.ensure_one()
        self.check_access("write")
        if (
            self.create_uid != self.env.user
            or self.company_id != self.incoming_document_id.company_id
        ):
            raise AccessError(self.env._("Use your own preview in its source company."))
        IncomingAccountPolicyService(self.env).check_access(self.incoming_document_id)

    @api.model
    def _preview_values(self, preview):
        document = preview.get("document", {})
        identity, totals = document.get("identity", {}), document.get("totals", {})
        values = {
            key: preview[key]
            for key in (
                "company_id",
                "incoming_document_id",
                "currency_id",
                "partner_id",
                "journal_id",
                "accounting_date",
                "mode",
            )
        }
        values.update(
            document_code=identity.get("document_code"),
            folio=identity.get("folio"),
            issue_date=identity.get("issue_date"),
            due_date=identity.get("due_date") or False,
            source_net=float(totals.get("net", 0)),
            source_exempt=float(totals.get("exempt", 0)),
            source_vat=float(totals.get("vat", 0)),
            source_total=float(totals.get("total", 0)),
            candidate_move_ids=[Command.set(preview.get("candidates", []))],
            diagnostic_text="\n".join(
                "{} [{}] {} {} / {}".format(
                    d["code"],
                    d["ordinal"],
                    d["message"],
                    d["source_value"],
                    d["odoo_value"],
                )
                for d in preview["diagnostics"]
            ),
            has_blockers=any(
                d["severity"] == "blocker" for d in preview["diagnostics"]
            ),
            preview_digest=preview.get("preview_digest") or False,
        )
        lines = []
        for mapped in preview["lines"]:
            source = mapped["source"]
            choices = {
                key: mapped.get(key, False)
                for key in (
                    "mapping_id",
                    "product_id",
                    "account_id",
                    "tax_id",
                    "uom_id",
                    "existing_line_id",
                )
            }
            choices.update(
                sequence=source["ordinal"],
                description=source["name"],
                source_codes=source["codes"],
                source_quantity=source["quantity"],
                source_unit_price=source["unit_price"],
                source_discount_percent=source["discount_percent"],
                source_unit=source["unit"],
                source_exempt=source["exempt"],
                source_amount=float(source["amount"]),
                diagnostic_text="\n".join(
                    d["message"]
                    for d in preview["diagnostics"]
                    if d["ordinal"] == source["ordinal"]
                ),
            )
            lines.append(Command.create(choices))
        values["line_ids"] = [Command.clear(), *lines]
        return values
