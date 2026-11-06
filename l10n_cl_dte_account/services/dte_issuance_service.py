# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import json

from odoo import _
from odoo.exceptions import AccessError, UserError


class DteIssuanceService:
    """Orchestrate durable DTE issuance independently of XML format."""

    def issue(self, source, adapter, document_code):
        """Keep fiscal preparation, evidence, folio and queue writes atomic."""
        source.ensure_one()
        self._check_issuance_access(source)
        source = source.sudo()
        with source.env.cr.savepoint():
            return self._issue(source, adapter, document_code)

    def prepare(self, source, adapter, document_code):
        """Sign a DTE and retain its evidence without dispatching it."""
        source.ensure_one()
        self._check_issuance_access(source)
        source = source.sudo()
        with source.env.cr.savepoint():
            return self._prepare(source, adapter, document_code)

    def _check_issuance_access(self, source):
        if not source.env.user.has_group("l10n_cl_dte_core.group_dte_user"):
            raise AccessError(_("Only a DTE user can issue electronic documents."))

    def _issue(self, source, adapter, document_code):
        document = self._prepare(source, adapter, document_code)
        return self._enqueue_prepared(source, adapter, document)

    def _prepare(self, source, adapter, document_code):
        source.ensure_one()
        document_model = source.env["l10n_cl.electronic.document"]
        company = source.company_id
        environment = company.dte_sii_environment
        source_key = adapter.source_key(source, document_code)
        document = document_model.search(
            [("company_id", "=", company.id), ("source_key", "=", source_key)],
            limit=1,
        )
        if document:
            return self._resume_prepared(document)

        adapter.validate_source(source, document_code)
        document = document_model.search(
            [("company_id", "=", company.id), ("source_key", "=", source_key)],
            limit=1,
        )
        if document:
            return self._resume_prepared(document)
        caf = adapter.select_caf(source, environment, document_code)
        source_values = adapter.document_source_values(source)
        extra_values = adapter.prepare_document_values(source, document_code)
        protected_values = {
            "name",
            "company_id",
            "document_code",
            "sii_environment",
            "source_key",
            "source_move_id",
            "source_pos_order_id",
            "preparation_state",
            "fiscal_state",
            "folio",
            "payload_digest",
        }
        allowed_source_values = {"source_move_id", "source_pos_order_id"}
        if (
            not isinstance(source_values, dict)
            or len(source_values) != 1
            or set(source_values) - allowed_source_values
            or not isinstance(extra_values, dict)
            or protected_values.intersection(extra_values)
        ):
            raise UserError(_("The DTE adapter returned invalid document metadata."))
        expected_source_field = {
            "account.move": "source_move_id",
            "pos.order": "source_pos_order_id",
        }.get(source._name)
        if set(source_values) != {expected_source_field}:
            raise UserError(_("The DTE adapter returned an invalid source relation."))
        document = document_model.sudo().create(
            {
                "name": source.name or source.display_name,
                "company_id": company.id,
                "document_code": document_code,
                "sii_environment": environment,
                "source_key": source_key,
                **source_values,
                **extra_values,
            }
        )
        folio = caf.sudo().action_reserve_folio(document, f"{source_key}:folio")
        snapshot = adapter.capture_snapshot(source, caf, folio.number, document_code)
        snapshot_bytes = json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("UTF-8")
        snapshot_artifact = adapter.create_artifact(
            source,
            document,
            "source_snapshot",
            snapshot_bytes,
            adapter.snapshot_producer_version,
        )
        document.sudo().action_record_source_snapshot(snapshot_artifact.id)
        snapshot["ted_xml"] = adapter.compose_ted(snapshot, caf)
        unsigned_xml = adapter.compose_unsigned_document(snapshot)
        unsigned_artifact = adapter.create_artifact(
            source,
            document,
            "unsigned_xml",
            unsigned_xml,
            adapter.artifact_producer_version,
        )
        document.sudo().action_record_transition(
            "prepared", "dte_xml_prepared", artifact_id=unsigned_artifact.id
        )
        signed_xml = adapter.sign_document(
            company, document.sii_environment, unsigned_xml
        )
        adapter.validate_document(signed_xml)
        signed_artifact = adapter.create_artifact(
            source,
            document,
            "signed_xml",
            signed_xml,
            adapter.artifact_producer_version,
        )
        document.sudo().action_record_transition(
            "signed", "dte_xml_signed", artifact_id=signed_artifact.id
        )
        folio.sudo().action_consume()
        return document

    def _enqueue_prepared(self, source, adapter, document):
        # Serialize queueing with signature revision creation so the request
        # always captures the current signature and freezes it before dispatch.
        document.env.cr.execute(
            "SELECT id FROM l10n_cl_electronic_document WHERE id = %s FOR UPDATE",
            [document.id],
        )
        document.invalidate_recordset()
        if document.preparation_state == "queued":
            return self._resume_existing(source, document)
        if document.preparation_state != "signed":
            raise UserError(_("Only a signed DTE can be queued for submission."))
        signed_artifacts = document._get_signed_artifact()
        if len(signed_artifacts) != 1:
            raise UserError(_("A signed DTE needs one signed XML artifact."))
        signed_artifacts.action_verify_integrity()
        signed_xml = base64.b64decode(signed_artifacts.content, validate=True)
        request_artifact = adapter.create_artifact(
            source,
            document,
            "sii_request",
            signed_xml,
            adapter.artifact_producer_version,
        )
        document.sudo().action_record_transition(
            "queued", "dte_submission_queued", artifact_id=request_artifact.id
        )
        submission = (
            source.env["l10n_cl.electronic.submission"]
            .sudo()
            .create(
                {
                    "document_id": document.id,
                    "environment": document.sii_environment,
                    "demo_scenario": (
                        source.env.context.get("dte_demo_scenario", "accepted")
                        if document.sii_environment == "demo"
                        else "accepted"
                    ),
                    "idempotency_key": f"{document.source_key}:submission",
                }
            )
        )
        submission.action_enqueue_submission()
        return document

    def _resume_prepared(self, document):
        if document.preparation_state not in {"signed", "queued"}:
            raise UserError(_("The existing DTE is not ready to resume."))
        return document

    def _resume_existing(self, source, document):
        if document.preparation_state != "queued":
            raise UserError(
                _("A DTE already exists for this source and is not ready to resend.")
            )
        submission = (
            source.env["l10n_cl.electronic.submission"]
            .sudo()
            .search([("document_id", "=", document.id)], limit=1)
        )
        if not submission:
            raise UserError(_("The existing DTE has no submission record."))
        submission.action_enqueue_submission()
        return document
