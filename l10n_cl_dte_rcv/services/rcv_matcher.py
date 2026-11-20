# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Reconcile RCV documents with journal entries and record each decision.

The matcher never writes journal entries: it links an RCV document to the
entry that carries its fiscal identity and classifies the result. It runs
with the rights of its environment.
"""

import logging

import psycopg2

from odoo.exceptions import AccessError, UserError, ValidationError

from .rcv_candidates import RcvCandidateFinder, is_generic_export
from .rcv_fiscal_evidence import RcvFiscalEvidence, rcv_amounts
from .rcv_tax_rules import RcvTaxRules, move_period

_logger = logging.getLogger(__name__)

# Errors that only mark one document for review; other documents continue.
MATCH_ERRORS = (
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    AccessError,
    UserError,
    ValidationError,
    psycopg2.Error,
)
COMPLETE_SECTION_STATE = "complete"


def section_complete(document):
    """Tell whether the read section that last reported ``document`` is complete."""
    sections = document.last_sync_id.section_ids.filtered(
        lambda section: section.sii_state == document.sii_state
    )
    return bool(sections) and all(
        section.state == COMPLETE_SECTION_STATE for section in sections
    )


class RcvMatcher:
    """Classify RCV documents against the journal entries of their company."""

    def __init__(self, env):
        self.env = env
        self.finder = RcvCandidateFinder(env)
        self.evidence = RcvFiscalEvidence(env)
        self.rules = RcvTaxRules(env, self.evidence)

    def can_read_moves(self):
        return self.env["account.move"].has_access("read")

    def reconcile(self, documents):
        """Reconcile each document in its own savepoint."""
        for document in documents:
            self._reconcile_safely(document)
        return documents

    def _reconcile_safely(self, document):
        try:
            with self.env.cr.savepoint():
                return self.reconcile_document(document)
        except MATCH_ERRORS as error:
            error_type = type(error).__name__
        _logger.info("RCV document %s not reconciled (%s).", document.id, error_type)
        document.write(
            self._outcome(
                "review",
                "internal_error",
                self.env._("The automatic reconciliation failed (%s).", error_type),
            )
        )
        self._log(document, document.move_id, {"error_type": error_type})
        return False

    def reconcile_document(self, document):
        values, move, evidence = self._evaluate(document)
        document.write(values)
        self._log(document, move, evidence)
        return document

    def _log(self, document, move, evidence):
        return self.env["l10n_cl.dte.rcv.match.log"]._log_automatic_decision(
            document, move, evidence
        )

    @staticmethod
    def _outcome(state, reason, message=False):
        return {
            "reconciliation_state": state,
            "reconciliation_reason_code": reason,
            "reconciliation_message": message or False,
        }

    def _base_values(self, document):
        amounts = rcv_amounts(document)
        return {
            "fiscal_vat_amount": amounts["fiscal_vat"] or 0.0,
            "tax_comparison_state": "not_comparable",
            "tax_allocation_state": "not_comparable",
            "tax_allocation_note": False,
        }

    # Decision

    def _evaluate(self, document):
        """Return ``(values, move, evidence)`` of one reconciliation decision."""
        env = self.env
        moves = env["account.move"]
        values = self._base_values(document)
        if is_generic_export(document):
            error = self.finder.export_identity_error(document)
            if error:
                values.update(self._outcome("review", "invalid_fiscal_identity", error))
                return values, moves, {}
        if document.manual_link_state == "unlinked":
            message = env._("A DTE manager unlinked this document; link it manually.")
            values.update(self._outcome("review", "manually_unlinked", message))
            return values, moves, {}
        candidates = document._get_rcv_candidate_moves()
        evidence = {"candidate_move_ids": candidates.ids}
        if (
            document.manual_link_state == "linked"
            and document.move_id not in candidates
        ):
            values["manual_link_state"] = False
        move, outcome = self._select_move(document, candidates)
        if outcome:
            values.update(outcome)
            return values, move, evidence
        values["move_id"] = move.id
        if is_generic_export(document):
            comparison, details = self._compare_export(document, move)
        else:
            comparison, details = self._compare(document, move)
        values.update(comparison)
        evidence.update(details)
        return values, move, evidence

    def _select_move(self, document, candidates):
        """Return ``(move, outcome)``; an outcome ends the decision."""
        env = self.env
        linked = document.move_id
        if document.manual_link_state == "linked" and linked in candidates:
            return linked, None
        if len(candidates) > 1:
            message = env._(
                "%s journal entries have this document type, folio and RUT.",
                len(candidates),
            )
            return candidates[:0], self._outcome(
                "duplicate", "duplicate_fiscal_identity", message
            )
        if not candidates:
            return candidates, self._missing_outcome(document)
        if linked and linked != candidates:
            message = env._(
                "The document is linked to another journal entry; review the "
                "link before reconciling."
            )
            return candidates, self._outcome(
                "review", "conflicting_existing_link", message
            )
        return candidates, None

    def _missing_outcome(self, document):
        env = self.env
        if not section_complete(document):
            message = env._(
                "No journal entry was found, but the RCV section that reported "
                "this document is incomplete: nothing is concluded."
            )
            outcome = self._outcome("indeterminate", "incomplete_rcv_section", message)
        else:
            outcome = self._outcome("missing", "odoo_document_missing")
        outcome["move_id"] = False
        return outcome

    # Comparison

    def _compare(self, document, move):
        """Compare amounts, VAT and period; return ``(values, evidence)``."""
        net, exempt, zero_tax_exempt = self.rules.booked_bases(document, move)
        amount_diff = self._amounts_differ(document, move, net, exempt)
        tax_state, tax_diff = self.rules.compare_vat(document, move)
        conflict = self.rules.zero_vat_conflict(document)
        source = self._source_evidence(document, move, conflict)
        confirmed = bool(conflict and source.get("verified"))
        if confirmed:
            tax_state, tax_diff, amount_diff = "source_confirmed", False, False
        elif zero_tax_exempt:
            tax_state, tax_diff = "matched", False
        period_diff = move_period(move) != document.tax_period
        values = self._fiscal_outcome(
            document,
            move,
            period_diff=period_diff,
            amount_diff=amount_diff or tax_diff,
            tax_state=tax_state,
            unresolved_residual=conflict and not confirmed,
            source=source,
        )
        values["tax_comparison_state"] = tax_state
        values.update(self._allocation_values(document, move, values))
        evidence = {
            "rcv": rcv_amounts(document),
            "booked": {
                "net": net,
                "exempt": exempt,
                "total": abs(move.amount_total_signed),
                "fiscal_vat": self.evidence.odoo_fiscal_vat(move),
            },
            "move_period": move_period(move),
            "source_evidence": source,
        }
        return values, evidence

    @staticmethod
    def _amounts_differ(document, move, net, exempt):
        currency = document.currency_id
        return any(
            not currency.is_zero(rcv_amount - booked_amount)
            for rcv_amount, booked_amount in (
                (document.net_amount, net),
                (document.exempt_amount, exempt),
                (document.total_amount, abs(move.amount_total_signed)),
            )
        )

    def _allocation_values(self, document, move, outcome):
        """Compare the VAT allocation; it only qualifies a fiscal match."""
        state, note = self.rules.compare_allocation(document, move)
        values = {"tax_allocation_state": state, "tax_allocation_note": note}
        if outcome["reconciliation_reason_code"] == "fiscal_match":
            if state == "different":
                values["reconciliation_reason_code"] = "tax_allocation_difference"
            elif state == "not_comparable":
                values["reconciliation_reason_code"] = "tax_allocation_unclassified"
        return values

    def _source_evidence(self, document, move, conflict):
        if not conflict:
            return {"verified": False, "reason_code": "not_required"}
        residual = rcv_amounts(document)["unexplained_residual"]
        return document._get_rcv_source_tax_evidence(move, residual)

    def _fiscal_outcome(self, document, move, **result):
        env = self.env
        if result["unresolved_residual"]:
            source = result["source"]
            message = env._(
                "The RCV declares zero VAT but leaves an unexplained residual of "
                "%(residual)s. Source evidence: %(evidence)s. The link was kept.",
                residual=rcv_amounts(document)["unexplained_residual"],
                evidence=source.get("message") or env._("none"),
            )
            return self._outcome("review", "unexplained_tax_residual", message)
        if result["period_diff"]:
            message = env._(
                "The journal entry is dated in period %(move)s; the RCV reports "
                "it in period %(rcv)s.",
                move=move_period(move),
                rcv=document.tax_period,
            )
            return self._outcome("period_diff", "accounting_period_mismatch", message)
        if result["tax_state"] == "not_comparable":
            message = env._(
                "The RCV tax breakdown could not be compared with identifiable "
                "booked taxes."
            )
            return self._outcome("review", "tax_mapping_unavailable", message)
        if result["amount_diff"]:
            message = env._(
                "The net, exempt, VAT or total amounts differ from the journal entry."
            )
            return self._outcome("amount_diff", "fiscal_amount_mismatch", message)
        if result["tax_state"] == "source_confirmed":
            return self._outcome("matched", "rcv_source_inconsistency_resolved")
        return self._outcome("matched", "fiscal_match")

    def _compare_export(self, document, move):
        """Compare a generic export with its posted company balances."""
        env = self.env
        if move.state != "posted":
            message = env._("The export invoice must be posted to compare its amounts.")
            return self._outcome("review", "export_not_posted", message), {}
        bases = self.rules.export_bases(move)
        if bases is None:
            message = env._(
                "The export invoice has a tax that cannot be classified as "
                "taxed or exempt."
            )
            return self._outcome("review", "tax_mapping_unavailable", message), {}
        net, exempt = bases
        period_diff = move_period(move) != document.tax_period
        tax_state, tax_diff = self.rules.compare_export_vat(document, move)
        if not period_diff and self.rules.proven_zero_tax_exempt(
            document, move, exempt
        ):
            tax_state, tax_diff = "matched", False
        amount_diff = tax_diff or self._amounts_differ(document, move, net, exempt)
        values = self._fiscal_outcome(
            document,
            move,
            period_diff=period_diff,
            amount_diff=amount_diff,
            tax_state=tax_state,
            unresolved_residual=False,
            source={},
        )
        values["tax_comparison_state"] = tax_state
        evidence = {
            "generic_export": True,
            "booked": {"net": net, "exempt": exempt},
            "move_period": move_period(move),
        }
        return values, evidence
