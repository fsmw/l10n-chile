# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import logging

from odoo import _, api, models

_logger = logging.getLogger(__name__)


class RcvAutoDraft(models.AbstractModel):
    _name = "l10n.cl.rcv.auto.draft"
    _description = "Create vendor drafts from complete RCV purchase details"

    @api.model
    def process_sync(self, sync):
        """Create one draft for each eligible Registro detail independently."""
        sync.ensure_one()
        if (
            sync.operation != "purchase"
            or sync.state not in ("success", "partial")
            or sync.source != "sii"
        ):
            return
        registration_sections = sync.section_ids.filtered(
            lambda section: (section.sii_state or "").strip().casefold() == "registro"
        )
        if not registration_sections or any(
            section.state != "complete" for section in registration_sections
        ):
            return

        documents = sync.document_ids.filtered(
            lambda document: (
                (document.sii_state or "").strip().casefold() == "registro"
                and document.operation == "purchase"
                and document.source == "sii"
            )
        )
        for document in documents:
            try:
                with self.env.cr.savepoint():
                    self._create_document_draft(document)
            except Exception as exc:
                _logger.exception(
                    "RCV draft creation failed for document %s", document.id
                )
                message = _(
                    "No se pudo crear el borrador automático: %(reason)s",
                    reason=str(exc)[:350],
                )
                if document.automation_error != message:
                    document.automation_error = message
                    document.write({"reconciliation_state": "review"})

    @api.model
    def process_existing_purchases(self, company, periods):
        """Backfill eligible SII documents already stored for selected periods."""
        if not company.l10n_cl_rcv_automation_enabled:
            return
        period_model = self.env["l10n.cl.rcv.period"].sudo()
        sync_model = self.env["l10n.cl.rcv.sync"].sudo()
        for year, month in periods:
            rcv_periods = period_model.search(
                [
                    ("company_id", "=", company.id),
                    ("year", "=", year),
                    ("month", "=", month),
                    ("operation", "=", "purchase"),
                ]
            )
            syncs = sync_model.search(
                [
                    ("period_id", "in", rcv_periods.ids),
                    ("company_id", "=", company.id),
                    ("operation", "=", "purchase"),
                    ("source", "=", "sii"),
                    ("state", "in", ("success", "partial")),
                ],
                order="create_date desc, id desc",
            )
            for sync in syncs:
                self.process_sync(sync)

    @api.model
    def _create_document_draft(self, document):
        identity = "|".join(
            str(value or "").strip().upper()
            for value in (
                document.company_id.id,
                document.operation,
                self.env["l10n.cl.rcv.matcher"]._normalize_vat(document.issuer_vat),
                document.document_type_code,
                document.folio.lstrip("0") or "0",
            )
        )
        self.env.cr.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (identity,))
        document.invalidate_recordset(["move_id", "reconciliation_state"])
        matcher = self.env["l10n.cl.rcv.matcher"]
        candidates = matcher._candidate_moves(document)
        if len(candidates) > 1:
            document.write({"reconciliation_state": "duplicate"})
            return
        if candidates:
            matcher._reconcile(document)
            return
        if document.move_id:
            document.write({"reconciliation_state": "review"})
            return

        wizard = self.env["l10n.cl.rcv.draft.wizard"].create(
            {"document_id": document.id}
        )
        # Interactive actions require reconciliation_state == "missing" so
        # users cannot create a duplicate from rows requiring review. The
        # automation already checked candidates above and must also retry rows
        # previously marked for review after a transient per-document error.
        wizard._autocomplete_from_rcv()
        action = wizard._create_draft()
        move = self.env["account.move"].browse(action.get("res_id"))
        if not move.exists():
            raise ValueError(_("El asistente no devolvió la factura creada."))
