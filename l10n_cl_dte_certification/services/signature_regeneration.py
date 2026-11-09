# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Replace signatures on never-submitted SET evidence without issuing new DTE."""

import base64
import hashlib

from lxml import etree

from odoo import _
from odoo.exceptions import UserError, ValidationError

from odoo.addons.l10n_cl_dte_signing.services.signature_service import (
    SignatureService,
)
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import DteXmlComposer

from .set_execution_service import CertificationSetExecutionService

SII_NAMESPACE = "http://www.sii.cl/SiiDte"
DS_NAMESPACE = "http://www.w3.org/2000/09/xmldsig#"


class CertificationSignatureRegenerationService:
    def regenerate(self, run):
        run.ensure_one()
        run._check_execution_access()
        # Share the submission action's lock: queued envelopes are frozen even
        # when the worker has not yet dispatched them.
        with run.env.cr.savepoint():
            run.env.cr.execute(
                "SELECT id FROM l10n_cl_dte_certification_run WHERE id = %s FOR UPDATE",
                [run.id],
            )
            run.invalidate_recordset()
            run.line_ids.invalidate_recordset()
            self._check_run(run)
            self._check_frozen_envelope_header(run)
            selected = run.line_ids.filtered("selected").sorted("sequence")
            documents = selected.document_id.sorted("id")
            for document in documents:
                run.env.cr.execute(
                    "SELECT id FROM l10n_cl_electronic_document "
                    "WHERE id = %s FOR UPDATE",
                    [document.id],
                )
                document.invalidate_recordset()
                document._check_signature_regeneration()
            previous = {
                "file": run.envelope_file,
                "filename": run.envelope_filename,
                "digest": run.envelope_digest,
            }
            if (
                previous["file"]
                and hashlib.sha256(
                    base64.b64decode(previous["file"], validate=True)
                ).hexdigest()
                != previous["digest"]
            ):
                raise ValidationError(
                    _("The exported envelope fingerprint has changed.")
                )
            profile = run.env["l10n_cl.dte.signing.profile"].search(
                [
                    ("company_id", "=", run.company_id.id),
                    ("sii_environment", "=", "certification"),
                    ("active", "=", True),
                ],
                limit=1,
            )
            if not profile:
                raise UserError(_("Configure an active Certification signing profile."))
            changes = [self._regenerate_document(document) for document in documents]
            execution = CertificationSetExecutionService()
            for line in selected:
                execution.validate_case_result(line)
                line._write_internal(
                    {
                        "state": "prepared",
                        "diagnostic_code": False,
                        "diagnostic_message": False,
                    }
                )
            run._write_internal(
                {
                    "state": "partial" if run.is_partial else "prepared",
                    "envelope_file": False,
                    "envelope_filename": False,
                    "envelope_digest": False,
                    "reportability_state": "not_checked",
                    "official_coverage_digest": False,
                    "reportability_diagnostics": False,
                    "reportability_warnings": False,
                    "diagnostics": False,
                }
            )
            execution.build_envelope(run)
            run.env["l10n_cl.dte.certification.signature.regeneration"]._record(
                run,
                profile.sudo().certificate_id,
                previous,
                changes,
            )
            # Configuration and token freshness checks only; no external I/O.
            run._run_official_preflight()
        return True

    @staticmethod
    def _check_frozen_envelope_header(run):
        snapshot = run.input_snapshot or {}
        current = {
            "sender_vat": run.set_id.sender_vat or "",
            "resolution_number": run.set_id.resolution_number or "",
            "resolution_date": run.set_id.resolution_date.isoformat()
            if run.set_id.resolution_date
            else "",
        }
        if any(
            key in snapshot and snapshot[key] != value for key, value in current.items()
        ):
            raise UserError(
                _("The envelope configuration changed after document generation.")
            )

    @staticmethod
    def _check_run(run):
        if run.set_id.state == "archived":
            raise UserError(
                _(
                    "Archived SETs are immutable. Reimport the source to create "
                    "a new SET record."
                )
            )
        if (
            run.set_id.state != "ready"
            or run.set_id.sii_environment != "certification"
            or run.state
            not in {
                "prepared",
                "partial",
                "exported",
            }
        ):
            raise UserError(
                _("Prepare the Certification DTE before regenerating signatures.")
            )
        if run.envelope_submission_ids or run.reportability_state in {
            "queued",
            "sending",
            "outcome_unknown",
            "transport_error",
            "submitted",
            "reconciled",
        }:
            raise UserError(
                _("Signatures cannot change after an SII envelope is queued.")
            )
        selected = run.line_ids.filtered("selected")
        if not selected or any(not line.document_id for line in selected):
            raise UserError(_("Every selected case needs an existing signed DTE."))
        if any(
            document.sii_environment != "certification"
            for document in selected.document_id
        ):
            raise UserError(
                _("Only Certification DTE can be regenerated from this set.")
            )

    @staticmethod
    def _regenerate_document(document):
        previous = document._get_signed_artifact()
        previous.action_verify_integrity()
        xml_bytes = base64.b64decode(previous.content, validate=True)
        root = etree.fromstring(
            xml_bytes,
            etree.XMLParser(
                resolve_entities=False,
                no_network=True,
                load_dtd=False,
            ),
        )
        fiscal_nodes = root.findall(f"{{{SII_NAMESPACE}}}Documento")
        signatures = root.findall(f"{{{DS_NAMESPACE}}}Signature")
        if len(fiscal_nodes) != 1 or len(signatures) != 1:
            raise ValidationError(
                _("A signed DTE needs one Documento and one signature.")
            )
        fiscal_bytes = etree.tostring(
            fiscal_nodes[0], encoding="UTF-8", with_tail=False
        )
        root.remove(signatures[0])
        unsigned = etree.tostring(root, encoding="ISO-8859-1", xml_declaration=True)
        signed = SignatureService().sign_document(
            document.company_id,
            "certification",
            unsigned,
        )
        DteXmlComposer().validate_document(signed)
        regenerated = etree.fromstring(
            signed, etree.XMLParser(resolve_entities=False, no_network=True)
        )
        if (
            etree.tostring(
                regenerated.find(f"{{{SII_NAMESPACE}}}Documento"),
                encoding="UTF-8",
                with_tail=False,
            )
            != fiscal_bytes
        ):
            raise ValidationError(
                _("Signature regeneration must preserve the fiscal XML exactly.")
            )
        artifact = document.env[
            "l10n_cl.electronic.artifact"
        ]._create_signature_revision(
            previous,
            signed,
            "l10n_cl_dte_certification/signature-regeneration/18.0",
        )
        document._create_audit_events(
            [
                {
                    "event_code": "signature_regenerated",
                    "state_domain": "preparation",
                    "from_state": "signed",
                    "to_state": "signed",
                    "artifact_id": artifact.id,
                    "correlation_key": previous.sha256,
                }
            ]
        )
        return {
            "document_id": document.id,
            "folio": document.folio,
            "previous_artifact_id": previous.id,
            "previous_digest": previous.sha256,
            "replacement_artifact_id": artifact.id,
            "replacement_digest": artifact.sha256,
        }
