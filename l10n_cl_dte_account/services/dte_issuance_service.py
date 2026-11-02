# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import json

from odoo import _
from odoo.exceptions import UserError


class DteIssuanceService:
    """Orchestrate durable DTE issuance independently of XML format."""

    def issue(self, move, adapter, document_code):
        """Keep fiscal preparation, evidence, folio and queue writes atomic."""
        move.ensure_one()
        with move.env.cr.savepoint():
            return self._issue(move, adapter, document_code)

    def _issue(self, move, adapter, document_code):
        move.ensure_one()
        document_model = move.env["l10n_cl.electronic.document"]
        environment = move.company_id.dte_sii_environment
        source_key = f"account.move:{move.id}:type-{document_code}"
        document = document_model.search(
            [("company_id", "=", move.company_id.id), ("source_key", "=", source_key)],
            limit=1,
        )
        if document:
            return self._resume_existing(move, document)

        adapter.validate_source(move, document_code)
        document = document_model.search(
            [("company_id", "=", move.company_id.id), ("source_key", "=", source_key)],
            limit=1,
        )
        if document:
            return self._resume_existing(move, document)
        caf = move._select_dte_caf(environment, document_code)
        extra_values = adapter.prepare_document_values(move, document_code)
        protected_values = {
            "name",
            "company_id",
            "document_code",
            "sii_environment",
            "source_key",
            "source_move_id",
            "preparation_state",
            "fiscal_state",
            "folio",
            "payload_digest",
        }
        if not isinstance(extra_values, dict) or protected_values.intersection(
            extra_values
        ):
            raise UserError(_("The DTE adapter returned invalid document metadata."))
        document = document_model.create(
            {
                "name": move.name or move.display_name,
                "company_id": move.company_id.id,
                "document_code": document_code,
                "sii_environment": environment,
                "source_key": source_key,
                "source_move_id": move.id,
                **extra_values,
            }
        )
        folio = caf.action_reserve_folio(document, f"{source_key}:folio")
        snapshot = adapter.capture_snapshot(move, caf, folio.number, document_code)
        snapshot_bytes = json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("UTF-8")
        snapshot_artifact = move.env["l10n_cl.electronic.artifact"].create(
            {
                "document_id": document.id,
                "role": "source_snapshot",
                "content": base64.b64encode(snapshot_bytes),
                "mimetype": "application/json",
                "encoding": "UTF-8",
                "producer_version": adapter.snapshot_producer_version,
            }
        )
        document.action_record_source_snapshot(snapshot_artifact.id)
        snapshot["ted_xml"] = adapter.compose_ted(snapshot, caf)
        unsigned_xml = adapter.compose_unsigned_document(snapshot)
        unsigned_artifact = move._create_dte_artifact(
            document, "unsigned_xml", unsigned_xml, adapter.artifact_producer_version
        )
        document.action_record_transition(
            "prepared", "dte_xml_prepared", artifact_id=unsigned_artifact.id
        )
        signed_xml = adapter.sign_document(
            move.company_id, document.sii_environment, unsigned_xml
        )
        adapter.validate_document(signed_xml)
        signed_artifact = move._create_dte_artifact(
            document, "signed_xml", signed_xml, adapter.artifact_producer_version
        )
        document.action_record_transition(
            "signed", "dte_xml_signed", artifact_id=signed_artifact.id
        )
        request_artifact = move._create_dte_artifact(
            document, "sii_request", signed_xml, adapter.artifact_producer_version
        )
        document.action_record_transition(
            "queued", "dte_submission_queued", artifact_id=request_artifact.id
        )
        submission = move.env["l10n_cl.electronic.submission"].create(
            {
                "document_id": document.id,
                "environment": document.sii_environment,
                "demo_scenario": (
                    move.env.context.get("dte_demo_scenario", "accepted")
                    if document.sii_environment == "demo"
                    else "accepted"
                ),
                "idempotency_key": f"{source_key}:submission",
            }
        )
        folio.action_consume()
        submission.action_enqueue_submission()
        return document

    def _resume_existing(self, move, document):
        if document.preparation_state != "queued":
            raise UserError(
                _(
                    "A DTE already exists for this invoice and is not ready "
                    "to resend."
                )
            )
        submission = move.env["l10n_cl.electronic.submission"].search(
            [("document_id", "=", document.id)], limit=1
        )
        if not submission:
            raise UserError(_("The existing DTE has no submission record."))
        submission.action_enqueue_submission()
        return document
