# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import _, api, fields, models


class RcvDocument(models.Model):
    _name = "l10n.cl.rcv.document"
    _description = "SII RCV document"
    _order = "tax_period desc, document_date desc, folio desc"
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True)
    company_id = fields.Many2one(
        "res.company", required=True, index=True, ondelete="cascade"
    )
    operation = fields.Selection(
        [("purchase", "Compras"), ("sale", "Ventas")], required=True, index=True
    )
    source = fields.Selection(
        [("sii", "SII"), ("csv", "CSV")], required=True, default="sii"
    )
    tax_period = fields.Char(required=True, index=True)
    document_type_code = fields.Char(required=True, index=True)
    document_type_name = fields.Char()
    issuer_vat = fields.Char(required=True, index=True)
    issuer_name = fields.Char()
    counterpart_vat = fields.Char(index=True)
    folio = fields.Char(required=True, index=True)
    document_date = fields.Date()
    accounting_date = fields.Date()
    sii_state = fields.Char(index=True)
    net_amount = fields.Monetary(currency_field="currency_id")
    exempt_amount = fields.Monetary(currency_field="currency_id")
    vat_amount = fields.Monetary(currency_field="currency_id")
    vat_not_recoverable = fields.Monetary(currency_field="currency_id")
    common_vat = fields.Monetary(currency_field="currency_id")
    tax_breakdown_available = fields.Boolean(copy=False)
    tax_comparison_state = fields.Selection(
        [
            ("not_comparable", "Impuestos no comparables"),
            ("matched", "Impuestos coinciden"),
            ("different", "Diferencia de impuestos"),
            ("source_confirmed", "DTE confirma; RCV inconsistente"),
        ],
        default="not_comparable",
        copy=False,
    )
    fiscal_vat_amount = fields.Monetary(
        string="IVA fiscal",
        currency_field="currency_id",
        readonly=True,
        copy=False,
    )
    tax_allocation_state = fields.Selection(
        [
            ("not_comparable", "Clasificación no comparable"),
            ("matched", "Clasificación coincide"),
            ("different", "Diferencia de clasificación"),
        ],
        string="Estado de clasificación tributaria",
        default="not_comparable",
        readonly=True,
        copy=False,
    )
    tax_allocation_note = fields.Char(
        string="Observación de clasificación tributaria", readonly=True, copy=False
    )
    total_amount = fields.Monetary(currency_field="currency_id")
    currency_id = fields.Many2one(related="company_id.currency_id", store=True)
    active = fields.Boolean(default=True, index=True)
    move_id = fields.Many2one(
        "account.move", index=True, check_company=True, ondelete="set null"
    )
    candidate_move_ids = fields.Many2many("account.move", compute="_compute_candidates")
    reconciliation_state = fields.Selection(
        [
            ("pending", "Pendiente de conciliar"),
            ("matched", "Conciliado"),
            ("missing", "Falta en Odoo"),
            ("duplicate", "Duplicado en Odoo"),
            ("period_diff", "Diferencia de período"),
            ("amount_diff", "Diferencia de importes"),
            ("review", "Revisión manual"),
            ("indeterminate", "Indeterminado"),
        ],
        default="pending",
        required=True,
        index=True,
    )
    last_sync_id = fields.Many2one("l10n.cl.rcv.sync", index=True, ondelete="set null")
    automation_error = fields.Char(copy=False, readonly=True)
    reconciliation_reason_code = fields.Char(
        string="Motivo de conciliación", readonly=True, copy=False, index=True
    )
    observation_ids = fields.One2many("l10n.cl.rcv.observation", "document_id")
    match_log_ids = fields.One2many("l10n.cl.rcv.match.log", "document_id")
    automatic_log_ids = fields.One2many(
        "l10n.cl.rcv.match.log",
        "document_id",
        domain=[("event_type", "=", "automatic")],
    )

    _sql_constraints = [
        (
            "fiscal_identity_uniq",
            "unique(company_id, operation, document_type_code, issuer_vat, folio)",
            "Este documento fiscal ya fue importado.",
        ),
    ]

    @api.depends("document_type_code", "folio", "issuer_vat")
    def _compute_name(self):
        for document in self:
            document.name = "{}-{} · {}".format(
                document.document_type_code or "",
                document.folio or "",
                document.issuer_vat or "",
            )

    def _compute_candidates(self):
        matcher = self.env["l10n.cl.rcv.matcher"]
        for document in self:
            document.candidate_move_ids = matcher._candidate_moves(document)

    def action_reconcile(self):
        matcher = self.env["l10n.cl.rcv.matcher"]
        for document in self:
            try:
                with self.env.cr.savepoint():
                    matcher._reconcile(document)
            except Exception as error:
                message = (
                    _("Falló la conciliación automática (%s).") % type(error).__name__
                )
                document.write(
                    {
                        "reconciliation_state": "review",
                        "reconciliation_reason_code": "internal_error",
                        "automation_error": message,
                    }
                )
                self.env["l10n.cl.rcv.match.log"]._log_automatic_decision(
                    document,
                    document.move_id,
                    "review",
                    {"error_type": type(error).__name__},
                )
        counts = {}
        for state, label in (
            ("matched", _("conciliados")),
            ("amount_diff", _("con diferencia de importes")),
            ("period_diff", _("con diferencia de período")),
            ("review", _("en revisión")),
            ("duplicate", _("duplicados")),
            ("missing", _("sin documento en Odoo")),
            ("indeterminate", _("indeterminados")),
        ):
            count = len(
                self.filtered(
                    lambda document, state=state: document.reconciliation_state == state
                )
            )
            if count:
                counts[label] = count
        allocation_observations = len(
            self.filtered(
                lambda document: document.reconciliation_state == "matched"
                and document.tax_allocation_state == "different"
            )
        )
        if allocation_observations:
            counts[_("con observación de clasificación tributaria")] = (
                allocation_observations
            )
        message = ", ".join(f"{count} {label}" for label, count in counts.items())
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Conciliación RCV terminada"),
                "message": message or _("No se procesaron documentos."),
                "type": (
                    "success"
                    if len(self)
                    == len(
                        self.filtered(
                            lambda document: document.reconciliation_state == "matched"
                        )
                    )
                    else "warning"
                ),
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def action_link_move(self):
        self.ensure_one()
        wizard = self.env["l10n.cl.rcv.link.wizard"].create({"document_id": self.id})
        return {
            "type": "ir.actions.act_window",
            "name": "Vincular factura Odoo",
            "res_model": wizard._name,
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_open_move(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": self.move_id.id,
            "view_mode": "form",
        }

    def action_create_draft(self):
        self.ensure_one()
        return (
            self.env["l10n.cl.rcv.draft.wizard"]
            .create({"document_id": self.id})
            .action_open()
        )
