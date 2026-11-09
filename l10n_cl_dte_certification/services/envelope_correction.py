# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib

from cryptography import x509
from lxml import etree

from odoo import _, fields
from odoo.exceptions import UserError

from odoo.addons.l10n_cl_dte_gateway.services.envelope_composer import (
    SiiEnvelopeComposer,
)
from odoo.addons.l10n_cl_dte_signing.services.certificate_identity import (
    certificate_sender_rut,
    normalize_rut,
)

from .rejected_document_correction import RejectedCertificationDocumentCorrection
from .set_execution_service import CertificationSetExecutionService


class CertificationEnvelopeCorrection:
    """Revise a fully rejected transmission, preserving folios and receipts."""

    def prepare(self, run):
        run.ensure_one()
        self.env = run.env
        run._check_execution_access()
        with run.env.cr.savepoint():
            run.env.cr.execute(
                "SELECT id FROM l10n_cl_dte_certification_run WHERE id = %s FOR UPDATE",
                [run.id],
            )
            run.invalidate_recordset()
            if not run.can_correct_sii_envelope:
                raise UserError(
                    _("Only a verified envelope rejection can be corrected.")
                )
            if not run.set_id.resolution_date:
                raise UserError(
                    _(
                        "Enter the resolution date published for this company "
                        "in the SII Certification registry."
                    )
                )
            proofs = [
                s._verified_schema_rejection() for s in run.envelope_submission_ids
            ]
            profile = run.env["l10n_cl.dte.signing.profile"].search(
                [
                    ("company_id", "=", run.company_id.id),
                    ("sii_environment", "=", "certification"),
                    ("active", "=", True),
                ]
            )
            if len(profile) != 1 or profile.is_synthetic:
                raise UserError(
                    _("Configure one non-synthetic Certification signing profile.")
                )
            certificate = profile.sudo().certificate_id
            sender = certificate_sender_rut(
                x509.load_pem_x509_certificate(
                    base64.b64decode(certificate.pem_certificate)
                )
            )
            previous_sender = run.set_id.sender_vat
            # Recover the historical company-as-sender default only. A different
            # explicit identity requires the operator to review configuration.
            if normalize_rut(previous_sender) not in {
                normalize_rut(run.company_id.vat),
                sender,
            }:
                raise UserError(
                    _("The SII sender RUT must match the signing certificate holder.")
                )
            run.company_id.dte_sii_sender_vat = sender
            previous_number = run.company_id.dte_sii_resolution_number
            previous = {
                "file": run.envelope_file,
                "filename": run.envelope_filename,
                "digest": run.envelope_digest,
            }
            if (
                hashlib.sha256(
                    base64.b64decode(previous["file"], validate=True)
                ).hexdigest()
                != previous["digest"]
            ):
                raise UserError(_("The exported envelope fingerprint has changed."))
            previous_root = etree.fromstring(
                base64.b64decode(previous["file"], validate=True),
                parser=etree.XMLParser(resolve_entities=False, no_network=True),
            )
            previous_date = previous_root.xpath(
                "string(./*[local-name()='SetDTE']/*[local-name()='Caratula']"
                "/*[local-name()='FchResol'])"
            )
            selected = run.line_ids.filtered("selected").sorted("sequence")
            if not selected or len(selected) != len(run.line_ids) or run.is_partial:
                raise UserError(_("A complete signed Certification set is required."))
            executor = CertificationSetExecutionService()
            documents = [executor.validate_case_result(line) for line in selected]
            document_changes = []
            if proofs[0]["code"] == "EPR":
                sent = etree.fromstring(
                    base64.b64decode(
                        run.envelope_submission_ids[:1].request_xml, validate=True
                    ),
                    parser=etree.XMLParser(resolve_entities=False, no_network=True),
                ).xpath("./*[local-name()='SetDTE']/*[local-name()='DTE']")
                if len(sent) != len(selected):
                    raise UserError(
                        _("The current DTE differs from its retained evidence.")
                    )
                correction = RejectedCertificationDocumentCorrection()
                for index, line in enumerate(selected):
                    documents[index], change = correction.prepare(
                        line, documents[index], sent[index]
                    )
                    document_changes.append(change)
                    executor.validate_case_result(line)
            corrected = SiiEnvelopeComposer().compose(
                run.company_id,
                documents,
                {
                    "id": f"SET-{run.set_id.attention_number}-{run.revision}",
                    "sender_vat": sender,
                    "resolution_number": run.set_id.resolution_number,
                    "resolution_date": run.set_id.resolution_date,
                },
            )
            run._write_internal(
                {
                    "envelope_file": base64.b64encode(corrected),
                    "envelope_digest": hashlib.sha256(corrected).hexdigest(),
                    "reportability_state": "not_checked",
                    "official_coverage_digest": False,
                    "reportability_diagnostics": False,
                    "reportability_warnings": False,
                }
            )
            run.env["l10n_cl.dte.certification.signature.regeneration"]._record(
                run,
                certificate,
                previous,
                [
                    {
                        "operation": "envelope_rejection_correction",
                        "receipts": proofs,
                        "previous_sender": previous_sender,
                        "replacement_sender": sender,
                        "company_resolution_number": previous_number,
                        "replacement_resolution_number": run.set_id.resolution_number,
                        "previous_resolution_date": previous_date,
                        "company_resolution_date": fields.Date.to_string(
                            run.company_id.dte_sii_resolution_date
                        ),
                        "replacement_resolution_date": fields.Date.to_string(
                            run.set_id.resolution_date
                        ),
                        "document_changes": document_changes,
                    }
                ],
            )
            run._run_official_preflight()
        return True
