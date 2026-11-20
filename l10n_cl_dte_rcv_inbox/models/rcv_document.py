# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from odoo import api, fields, models
from odoo.exceptions import AccessError

from odoo.addons.l10n_cl_dte_inbox_account.services.common import (
    BINDING,
    VENDOR_DOCUMENT_CODES,
    canonical_folio,
    vat,
)
from odoo.addons.l10n_cl_dte_rcv.services.rcv_candidates import PURCHASE_MOVE_TYPES

from ..services.rcv_draft_service import MANAGER_GROUP, RcvDraftService
from ..services.rcv_source_evidence import RcvSourceEvidence

_logger = logging.getLogger(__name__)

# Largest folio an incoming DTE can store (PostgreSQL integer).
MAX_INCOMING_FOLIO = 2**31 - 1
DRAFT_BATCH_SIZE = 200
REGISTERED_STATE = "REGISTRO"


class RcvDocument(models.Model):
    _inherit = "l10n_cl.dte.rcv.document"

    incoming_dte_id = fields.Many2one(
        "l10n_cl.incoming.dte",
        string="Received DTE",
        index=True,
        copy=False,
        readonly=True,
        ondelete="set null",
        check_company=True,
        help="The DTE of the inbox with the issuer RUT, document type and folio "
        "of this purchase.",
    )
    dte_rcv_draft_error = fields.Char(
        string="Draft Preparation Error", copy=False, readonly=True
    )

    # Received DTE

    def _find_rcv_incoming_dte(self):
        """Return the received DTE with the fiscal identity of this purchase.

        Only DTEs received in the current SII environment of the company count.
        """
        self.ensure_one()
        incoming = self.env["l10n_cl.incoming.dte"]
        folio = canonical_folio(self.folio)
        if self.operation != "purchase" or not folio or int(folio) > MAX_INCOMING_FOLIO:
            return incoming
        company = self.company_id
        issuer = vat(self.issuer_vat)
        matches = (
            incoming.sudo()
            .search(
                [
                    ("company_id", "=", company.id),
                    ("sii_environment", "=", company.dte_sii_environment),
                    ("document_code", "=", self.document_type_code),
                    ("folio", "=", int(folio)),
                ]
            )
            .filtered(lambda record: vat(record.issuer_vat) == issuer)
        )
        return incoming.browse(matches[:1].ids)

    def _link_rcv_incoming_dte(self):
        """Store the received DTE of each purchase; return the found DTEs."""
        found = self.env["l10n_cl.incoming.dte"]
        for document in self.filtered(lambda item: item.operation == "purchase"):
            incoming = document._find_rcv_incoming_dte()
            if document.incoming_dte_id != incoming:
                # A derived link, not a user decision: any reader may store it.
                document.sudo().incoming_dte_id = incoming
            found |= incoming
        return found

    # P3 extension points

    def _get_rcv_candidate_moves(self):
        """Add the vendor bills bound to the received DTE of this purchase."""
        moves = super()._get_rcv_candidate_moves()
        return moves | self._get_rcv_inbox_bound_moves()

    def _get_rcv_inbox_bound_moves(self):
        self.ensure_one()
        moves = self.env["account.move"]
        if self.document_type_code not in VENDOR_DOCUMENT_CODES:
            return moves
        incoming = self._find_rcv_incoming_dte()
        if not incoming:
            return moves
        bindings = (
            self.env[BINDING]
            .sudo()
            .search([("incoming_document_id", "=", incoming.id)])
        )
        return moves.browse(bindings.move_id.ids).filtered_domain(
            [
                ("company_id", "=", self.company_id.id),
                ("move_type", "in", PURCHASE_MOVE_TYPES),
                ("state", "!=", "cancel"),
            ]
        )

    def _get_rcv_source_tax_evidence(self, move, residual):
        """Verify the VAT with the signed XML of the received DTE of ``move``."""
        evidence = RcvSourceEvidence(self.env).verify(self, move, residual)
        return evidence or super()._get_rcv_source_tax_evidence(move, residual)

    # Actions

    def action_create_rcv_draft(self):
        """Open the preparation of the vendor bill draft of this purchase."""
        self.ensure_one()
        service = RcvDraftService(self.env)
        service.check_allowed(self)
        service.check_eligible(self)
        self._link_rcv_incoming_dte()
        wizard = self.env["l10n_cl.dte.rcv.draft.wizard"].create(
            {"document_id": self.id}
        )
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Prepare Vendor Bill Draft"),
            "res_model": wizard._name,
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_link_rcv_incoming_dte(self):
        """Look up the received DTE of the selected purchases again."""
        if not self.env.user.has_group(MANAGER_GROUP):
            raise AccessError(self.env._("Only a DTE manager can link received DTEs."))
        self.check_access("write")
        self._link_rcv_incoming_dte()
        return True

    def action_open_rcv_incoming_dte(self):
        self.ensure_one()
        return self.incoming_dte_id.action_open()

    # Automatic drafts

    @api.model
    def _cron_create_rcv_drafts(self, batch_size=DRAFT_BATCH_SIZE):
        """Prepare the drafts of the purchases missing in accounting.

        Only companies that enabled automatic drafts are processed, on behalf
        of their draft user and with its access rights. Drafts are never
        posted.
        """
        companies = (
            self.env["res.company"].sudo().search([("dte_rcv_auto_draft", "=", True)])
        )
        created = 0
        for company in companies:
            if not company._dte_rcv_draft_user_is_valid():
                _logger.warning(
                    "Automatic RCV drafts skipped for company %s: its draft user "
                    "is inactive, lacks the required roles or the company.",
                    company.id,
                )
                continue
            created += self._create_company_rcv_drafts(company, batch_size)
        return created

    @api.model
    def _create_company_rcv_drafts(self, company, batch_size):
        user = company.dte_rcv_draft_user_id
        env = self.env(
            user=user,
            context=dict(self.env.context, allowed_company_ids=[company.id]),
        )
        documents = env[self._name].search(
            [
                ("company_id", "=", company.id),
                ("operation", "=", "purchase"),
                ("sii_state", "=", REGISTERED_STATE),
                ("reconciliation_state", "=", "missing"),
                ("document_type_code", "in", sorted(VENDOR_DOCUMENT_CODES)),
            ],
            order="tax_period, id",
            limit=batch_size,
        )
        service = RcvDraftService(env)
        return len(
            [
                document
                for document in documents
                if service.create_automatic_draft(document)
            ]
        )
