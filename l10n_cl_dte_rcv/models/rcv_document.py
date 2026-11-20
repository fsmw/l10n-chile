# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from collections import Counter

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services.rcv_candidates import RcvCandidateFinder
from ..services.rcv_matcher import RcvMatcher
from .rcv_period import DOCUMENT_OPERATIONS, MANAGER_GROUP, RCV_SOURCES

ACCOUNTING_GROUPS = "account.group_account_invoice,account.group_account_readonly"
RECONCILIATION_STATES = [
    ("pending", "Pending"),
    ("matched", "Matched"),
    ("missing", "Missing in Accounting"),
    ("duplicate", "Duplicate in Accounting"),
    ("period_diff", "Period Difference"),
    ("amount_diff", "Amount Difference"),
    ("review", "Manual Review"),
    ("indeterminate", "Indeterminate"),
]
REASON_CODES = [
    ("fiscal_match", "Fiscal match"),
    ("tax_allocation_difference", "VAT allocation differs"),
    ("tax_allocation_unclassified", "VAT allocation not classified"),
    ("rcv_source_inconsistency_resolved", "Source document confirms the VAT"),
    ("unexplained_tax_residual", "Unexplained tax residual"),
    ("accounting_period_mismatch", "Accounting period differs"),
    ("tax_mapping_unavailable", "Taxes not comparable"),
    ("fiscal_amount_mismatch", "Amounts differ"),
    ("duplicate_fiscal_identity", "Several journal entries match"),
    ("odoo_document_missing", "No journal entry"),
    ("incomplete_rcv_section", "Incomplete RCV section"),
    ("conflicting_existing_link", "Linked to another journal entry"),
    ("invalid_fiscal_identity", "Invalid fiscal identity"),
    ("export_not_posted", "Export invoice not posted"),
    ("manually_unlinked", "Manually unlinked"),
    ("internal_error", "Reconciliation failed"),
]


class RcvDocument(models.Model):
    """A fiscal document as registered in the SII RCV.

    The fiscal identity is company, operation, document type, issuer RUT and
    folio. Each read updates the document and adds an observation.
    """

    _name = "l10n_cl.dte.rcv.document"
    _description = "SII RCV Document"
    _order = "tax_period desc, document_date desc, folio desc"
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True)
    company_id = fields.Many2one(
        "res.company", required=True, index=True, ondelete="cascade"
    )
    operation = fields.Selection(DOCUMENT_OPERATIONS, required=True, index=True)
    source = fields.Selection(RCV_SOURCES, required=True, default="sii")
    tax_period = fields.Char(required=True, index=True, help="YYYYMM")
    document_type_code = fields.Char(required=True, index=True)
    document_type_name = fields.Char()
    issuer_vat = fields.Char(string="Issuer RUT", required=True, index=True)
    issuer_name = fields.Char()
    counterpart_vat = fields.Char(string="Counterpart RUT", index=True)
    counterpart_name = fields.Char()
    folio = fields.Char(required=True, index=True)
    document_date = fields.Date()
    accounting_date = fields.Date()
    sii_state = fields.Char(string="SII State", index=True)
    net_amount = fields.Monetary(currency_field="currency_id")
    exempt_amount = fields.Monetary(currency_field="currency_id")
    vat_amount = fields.Monetary(string="VAT Amount", currency_field="currency_id")
    vat_not_recoverable = fields.Monetary(
        string="Non-Recoverable VAT", currency_field="currency_id"
    )
    common_vat = fields.Monetary(string="Common Use VAT", currency_field="currency_id")
    tax_breakdown_available = fields.Boolean(
        string="Complete Tax Breakdown",
        copy=False,
        help="The read reported recoverable, non-recoverable and common use VAT.",
    )
    total_amount = fields.Monetary(currency_field="currency_id")
    currency_id = fields.Many2one(related="company_id.currency_id", store=True)
    active = fields.Boolean(default=True, index=True)
    last_sync_id = fields.Many2one(
        "l10n_cl.dte.rcv.sync",
        string="Last Read",
        index=True,
        ondelete="set null",
        check_company=True,
    )
    observation_ids = fields.One2many("l10n_cl.dte.rcv.observation", "document_id")
    move_id = fields.Many2one(
        "account.move",
        string="Journal Entry",
        index=True,
        copy=False,
        readonly=True,
        ondelete="set null",
        check_company=True,
        groups=ACCOUNTING_GROUPS,
    )
    candidate_move_ids = fields.Many2many(
        "account.move",
        string="Candidate Entries",
        compute="_compute_candidate_move_ids",
        groups=ACCOUNTING_GROUPS,
        help="Journal entries with the document type, folio and RUT of this "
        "document.",
    )
    has_move = fields.Boolean(string="Linked to an Entry", compute="_compute_has_move")
    manual_link_state = fields.Selection(
        [("linked", "Manually Linked"), ("unlinked", "Manually Unlinked")],
        copy=False,
        readonly=True,
        help="A manual decision that the automatic reconciliation respects.",
    )
    reconciliation_state = fields.Selection(
        RECONCILIATION_STATES,
        default="pending",
        required=True,
        index=True,
        copy=False,
        readonly=True,
    )
    reconciliation_reason_code = fields.Selection(
        REASON_CODES,
        string="Reconciliation Reason",
        index=True,
        copy=False,
        readonly=True,
    )
    reconciliation_message = fields.Char(copy=False, readonly=True)
    tax_comparison_state = fields.Selection(
        [
            ("not_comparable", "Not Comparable"),
            ("matched", "Matched"),
            ("different", "Different"),
            ("source_confirmed", "Confirmed by the Source Document"),
        ],
        string="VAT Comparison",
        default="not_comparable",
        copy=False,
        readonly=True,
    )
    fiscal_vat_amount = fields.Monetary(
        string="Fiscal VAT",
        currency_field="currency_id",
        copy=False,
        readonly=True,
        help="Recoverable, non-recoverable and common use VAT reported by the RCV.",
    )
    tax_allocation_state = fields.Selection(
        [
            ("not_comparable", "Not Comparable"),
            ("matched", "Matched"),
            ("different", "Different"),
        ],
        string="VAT Allocation",
        default="not_comparable",
        copy=False,
        readonly=True,
    )
    tax_allocation_note = fields.Char(
        string="VAT Allocation Note", copy=False, readonly=True
    )
    match_log_ids = fields.One2many(
        "l10n_cl.dte.rcv.match.log", "document_id", string="Reconciliation Log"
    )

    _sql_constraints = [
        (
            "fiscal_identity_uniq",
            "unique(company_id, operation, document_type_code, issuer_vat, folio)",
            "This fiscal document is already registered.",
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

    @api.depends("move_id")
    def _compute_has_move(self):
        for document in self:
            document.has_move = bool(document.sudo().move_id)

    @api.depends(
        "company_id",
        "operation",
        "document_type_code",
        "issuer_vat",
        "counterpart_vat",
        "folio",
        "reconciliation_state",
        "move_id",
    )
    def _compute_candidate_move_ids(self):
        for document in self:
            document.candidate_move_ids = document._get_rcv_candidate_moves()

    # Extension points

    def _get_rcv_candidate_moves(self):
        """Return the journal entries that carry the identity of this document.

        Addons may add entries found through other evidence, such as the
        received DTE of a vendor bill.
        """
        self.ensure_one()
        return RcvCandidateFinder(self.env).candidate_moves(self)

    def _get_rcv_source_tax_evidence(self, move, residual):
        """Verify the source DTE of a purchase that declares zero VAT.

        Return a dict with ``verified`` and a ``message``. The RCV alone never
        explains a residual, so the base implementation verifies nothing.
        """
        self.ensure_one()
        return {
            "verified": False,
            "reason_code": "missing_source_document",
            "message": self.env._("no verified source DTE"),
            "residual": residual,
        }

    # Actions

    def _check_reconcile_allowed(self):
        if not self.env.user.has_group(MANAGER_GROUP):
            raise AccessError(
                self.env._("Only a DTE manager can reconcile RCV documents.")
            )
        if self.company_id - self.env.companies:
            raise UserError(
                self.env._("Select one of the companies you are working in.")
            )
        self.check_access("write")
        self.env["account.move"].check_access("read")

    def action_reconcile(self):
        """Reconcile the selected documents and report the result."""
        self._check_reconcile_allowed()
        RcvMatcher(self.env).reconcile(self)
        labels = dict(
            self._fields["reconciliation_state"]._description_selection(self.env)
        )
        counts = Counter(self.mapped("reconciliation_state"))
        summary = [
            f"{counts[state]} {labels[state]}"
            for state, _label in RECONCILIATION_STATES
            if counts[state]
        ]
        all_matched = all(doc.reconciliation_state == "matched" for doc in self)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": self.env._("RCV reconciliation finished"),
                "message": ", ".join(summary)
                or self.env._("No document was processed."),
                "type": "success" if self and all_matched else "warning",
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def _open_link_wizard(self, mode):
        self.ensure_one()
        self._check_reconcile_allowed()
        wizard = self.env["l10n_cl.dte.rcv.link.wizard"].create(
            {"document_id": self.id, "mode": mode}
        )
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Link Journal Entry")
            if mode == "link"
            else self.env._("Unlink Journal Entry"),
            "res_model": wizard._name,
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_link_move(self):
        return self._open_link_wizard("link")

    def action_unlink_move(self):
        return self._open_link_wizard("unlink")

    def action_open_move(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": self.move_id.id,
            "view_mode": "form",
        }

    def _link_move(self, move, reason):
        """Link ``move`` manually, record why, and compare it at once."""
        self.ensure_one()
        self._check_reconcile_allowed()
        if move.company_id != self.company_id:
            raise UserError(
                self.env._("The journal entry must belong to the document company.")
            )
        if move not in self._get_rcv_candidate_moves():
            raise UserError(
                self.env._(
                    "The journal entry does not have the document type, folio and "
                    "RUT of the RCV document."
                )
            )
        self._create_manual_log("manual_link", move, reason)
        self.write({"move_id": move.id, "manual_link_state": "linked"})
        RcvMatcher(self.env).reconcile(self)
        return True

    def _unlink_move(self, reason):
        """Remove the link manually; the automatic reconciliation keeps it off."""
        self.ensure_one()
        self._check_reconcile_allowed()
        self._create_manual_log("manual_unlink", self.env["account.move"], reason)
        self.write(
            {
                "move_id": False,
                "manual_link_state": "unlinked",
                "reconciliation_state": "review",
                "reconciliation_reason_code": "manually_unlinked",
                "reconciliation_message": False,
            }
        )
        return True

    def _create_manual_log(self, event_type, move, reason):
        return self.env["l10n_cl.dte.rcv.match.log"].create(
            {
                "document_id": self.id,
                "event_type": event_type,
                "previous_move_id": self.move_id.id,
                "move_id": move.id,
                "reconciliation_state": self.reconciliation_state,
                "reconciliation_reason_code": self.reconciliation_reason_code,
                "reason": reason,
            }
        )
