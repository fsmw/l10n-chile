# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).


import base64
import hashlib

from odoo import _, fields
from odoo.exceptions import UserError, ValidationError

from odoo.addons.l10n_cl_dte_core.tools.xml_safety import safe_fromstring
from odoo.addons.l10n_cl_dte_gateway.services.envelope_composer import (
    SiiEnvelopeComposer,
)
from odoo.addons.l10n_cl_dte_signing.services.signature_service import (
    SignatureService,
    _without_xml_declaration,
)
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import _extract_dd_bytes

from .completion_policy import (
    catalogue_activity_matches_xml,
    catalogue_completions_allowed,
    has_generated_completion,
)
from .set_execution_service import CertificationSetExecutionService
from .set_parser import CertificationSetParser


class OfficialCertificationSetPreflight:
    """Validate full SII assigned-set coverage before an official submission."""

    def check_run(self, run):
        """Return immutable coverage digest and blocking diagnostics for a run."""
        run.ensure_one()
        diagnostics = self._coverage_diagnostics(run)
        warnings = []
        caf_model = run.env["l10n_cl.caf.authorization"]
        seen_receivers = set()
        executor = CertificationSetExecutionService()
        for line in run.line_ids.sorted("sequence"):
            diagnostics.extend(
                self._line_diagnostics(line, caf_model, seen_receivers, executor)
            )
            caf_warning = self._caf_warning(line)
            if caf_warning:
                warnings.append(caf_warning)
        diagnostics.extend(self._envelope_diagnostics(run))
        return {
            "digest": self._coverage_digest(run),
            "diagnostics": diagnostics,
            "warnings": warnings,
        }

    @staticmethod
    def _coverage_diagnostics(run):
        diagnostics = []
        certification_set = run.set_id
        certification_set._check_certification_access()
        certification_set._verify_source_integrity()
        if (
            getattr(certification_set, "completion_mode", "official_sii")
            != "official_sii"
        ):
            diagnostics.append({"code": "local_validation_only"})
        diagnostics.extend(
            OfficialCertificationSetPreflight._synthetic_completion_diagnostics(
                certification_set
            )
        )
        if run.state != "exported":
            diagnostics.append({"code": "execution_not_exported"})
        if run.is_partial:
            diagnostics.append({"code": "partial_execution"})
        source_cases = certification_set.case_ids.sorted("sequence")
        run_lines = run.line_ids.sorted("sequence")
        if len(source_cases) != len(run_lines):
            diagnostics.append({"code": "case_coverage_mismatch"})
        for expected, line in zip(source_cases, run_lines, strict=False):
            if line.case_id != expected or line.sequence != expected.sequence:
                diagnostics.append(
                    {"code": "case_order_mismatch", "case": expected.case_identifier}
                )
        if len({line.case_id.id for line in run_lines}) != len(run_lines):
            diagnostics.append({"code": "duplicate_case"})
        if len(run_lines) != len(source_cases):
            diagnostics.append({"code": "missing_or_extra_case"})
        if any(not line.selected for line in run_lines):
            diagnostics.append({"code": "unselected_case"})
        if any(line.state != "prepared" or not line.document_id for line in run_lines):
            diagnostics.append({"code": "case_dte_missing_or_failed"})
        if run.issue_date != fields.Date.context_today(run):
            diagnostics.append({"code": "issue_date_not_current"})
        if not run.envelope_file or not run.envelope_digest:
            diagnostics.append({"code": "envelope_missing"})
        elif (
            hashlib.sha256(base64.b64decode(run.envelope_file)).hexdigest()
            != run.envelope_digest
        ):
            diagnostics.append({"code": "envelope_digest_mismatch"})
        return diagnostics

    @staticmethod
    def _synthetic_completion_diagnostics(certification_set):
        if (
            getattr(certification_set, "contains_synthetic_completion", False)
            or has_generated_completion(certification_set)
        ) and not catalogue_completions_allowed(certification_set):
            return [{"code": "set_contains_synthetic_completion"}]
        return []

    def _line_diagnostics(self, line, caf_model, seen_receivers, executor):
        diagnostics = []
        case = line.case_id
        code = case.document_code
        if code not in {"33", "34", "56", "61"}:
            return [{"code": "unsupported_document_type", "case": case.case_identifier}]
        if not line.document_id or not line.move_id:
            return diagnostics
        document = line.document_id
        if document.sii_environment != "certification":
            diagnostics.append(
                {"code": "wrong_dte_environment", "case": case.case_identifier}
            )
        if document.document_code != code or document.source_move_id != line.move_id:
            diagnostics.append(
                {"code": "dte_identity_mismatch", "case": case.case_identifier}
            )
        if document.preparation_state != "signed" or document.fiscal_state != "unknown":
            diagnostics.append(
                {
                    "code": "dte_not_signed_or_already_submitted",
                    "case": case.case_identifier,
                }
            )
        diagnostics.extend(self._receiver_diagnostics(line, caf_model, seen_receivers))
        diagnostics.extend(self._caf_diagnostics(line))
        diagnostics.extend(self._signature_diagnostics(line, executor))
        return diagnostics

    @staticmethod
    def _receiver_diagnostics(line, caf_model, seen_receivers):
        case = line.case_id
        receiver = line.receiver_partner_id
        normalized_rut = caf_model._normalize_vat(line.receiver_rut_snapshot)
        diagnostics = []
        if (
            not receiver
            or receiver != case.partner_id
            or not line.receiver_rut_snapshot
            or normalized_rut != caf_model._normalize_vat(receiver.vat)
            or not caf_model._is_valid_vat(normalized_rut)
        ):
            diagnostics.append(
                {"code": "receiver_snapshot_invalid", "case": case.case_identifier}
            )
        valid_note_chain_duplicate = False
        if case.document_code in {"56", "61"}:
            target = case.set_id.case_ids.filtered(
                lambda item: item.case_identifier == case.reference_case_identifier
            )
            valid_note_chain_duplicate = bool(target and target.partner_id == receiver)
        if normalized_rut in seen_receivers and not valid_note_chain_duplicate:
            diagnostics.append(
                {"code": "receiver_rut_not_unique", "case": case.case_identifier}
            )
        seen_receivers.add(normalized_rut)
        return diagnostics

    @staticmethod
    def _caf_diagnostics(line):
        code = line.case_id.document_code
        certification_set = line.run_id.set_id
        document = line.document_id
        caf = certification_set._caf_for_document_code(code, document.folio)
        if (
            not caf
            or caf.company_id != line.company_id
            or caf.document_code != code
            or caf.sii_environment != "certification"
            or caf.is_synthetic
            or caf.state not in {"validated", "exhausted"}
            or not (caf.folio_start <= document.folio <= caf.folio_end)
        ):
            return [
                {
                    "code": "caf_folio_or_frma_invalid",
                    "case": line.case_id.case_identifier,
                }
            ]
        try:
            caf._check_caf_integrity()
        except (UserError, ValidationError, ValueError, TypeError):
            return [
                {
                    "code": "caf_folio_or_frma_invalid",
                    "case": line.case_id.case_identifier,
                }
            ]
        return []

    @staticmethod
    def _caf_warning(line):
        """Report unavailable FRMA authority verification without blocking local use."""
        code = line.case_id.document_code
        certification_set = line.run_id.set_id
        caf = certification_set._caf_for_document_code(code, line.document_id.folio)
        if caf and caf.sii_signature_status == "key_unavailable":
            return {
                "code": "caf_frma_not_verified",
                "case": line.case_id.case_identifier,
            }
        return False

    @staticmethod
    def _signature_diagnostics(line, executor):
        try:
            xml_bytes = executor.validate_case_result(line)
            SignatureService().verify_signature(xml_bytes, sii_standalone=True)
            root = safe_fromstring(xml_bytes)
            if not OfficialCertificationSetPreflight._signed_parties_match(root, line):
                raise UserError(
                    _("The signed DTE parties differ from the certification snapshot.")
                )
            if not catalogue_activity_matches_xml(root, line.case_id):
                raise UserError(
                    _(
                        "The signed DTE activity or correction differs from its "
                        "catalogue data."
                    )
                )
            ted = root.xpath("//*[local-name()='TED']")
            if len(ted) != 1:
                raise UserError(_("The signed DTE must contain exactly one TED."))
            dd = ted[0].xpath("./*[local-name()='DD']")
            frmt = ted[0].xpath("string(./*[local-name()='FRMT'])")
            caf_nodes = ted[0].xpath("./*[local-name()='DD']/*[local-name()='CAF']")
            if len(dd) != 1 or len(caf_nodes) != 1:
                raise UserError(_("The TED does not contain the expected CAF."))
            if not OfficialCertificationSetPreflight._embedded_caf_matches(
                caf_nodes[0], line
            ):
                raise UserError(_("The signed TED CAF differs from its authorization."))
            SignatureService().verify_ted(
                _extract_dd_bytes(xml_bytes), frmt, caf_nodes[0]
            )
        except (UserError, ValidationError, ValueError, TypeError) as error:
            return [
                {
                    "code": "signed_xml_validation_failed",
                    "case": line.case_id.case_identifier,
                    "detail": str(error)[:240],
                }
            ]
        return []

    @staticmethod
    def _signed_parties_match(root, line):
        document = root.xpath("./*[local-name()='Documento']")
        if len(document) != 1:
            return False
        issuer = document[0].xpath(
            "string(./*[local-name()='Encabezado']/*[local-name()='Emisor']/*[local-name()='RUTEmisor'])"
        )
        receiver = document[0].xpath(
            "string(./*[local-name()='Encabezado']/*[local-name()='Receptor']/*[local-name()='RUTRecep'])"
        )
        caf_model = line.env["l10n_cl.caf.authorization"]
        return caf_model._normalize_vat(issuer) == caf_model._normalize_vat(
            line.company_id.partner_id.vat
        ) and caf_model._normalize_vat(receiver) == caf_model._normalize_vat(
            line.receiver_rut_snapshot
        )

    @staticmethod
    def _embedded_caf_matches(embedded_caf, line):
        code = line.case_id.document_code
        authorization = line.run_id.set_id._caf_for_document_code(
            code, line.document_id.folio
        )
        if not authorization:
            return False
        key_id = embedded_caf.xpath(
            "string(./*[local-name()='DA']/*[local-name()='IDK'])"
        )
        document_code = embedded_caf.xpath(
            "string(./*[local-name()='DA']/*[local-name()='TD'])"
        )
        issuer = embedded_caf.xpath(
            "string(./*[local-name()='DA']/*[local-name()='RE'])"
        )
        folio_start = embedded_caf.xpath(
            "string(./*[local-name()='DA']/*[local-name()='RNG']/*[local-name()='D'])"
        )
        folio_end = embedded_caf.xpath(
            "string(./*[local-name()='DA']/*[local-name()='RNG']/*[local-name()='H'])"
        )
        caf_model = line.env["l10n_cl.caf.authorization"]
        return (
            key_id == authorization.caf_key_id
            and document_code == authorization.document_code
            and caf_model._normalize_vat(issuer)
            == caf_model._normalize_vat(authorization.issuer_vat)
            and folio_start == str(authorization.folio_start)
            and folio_end == str(authorization.folio_end)
        )

    @staticmethod
    def _envelope_diagnostics(run):
        diagnostics = []
        profile = run.env["l10n_cl.dte.signing.profile"].search(
            [
                ("company_id", "=", run.company_id.id),
                ("sii_environment", "=", "certification"),
                ("active", "=", True),
            ]
        )
        if (
            len(profile) != 1
            or not profile.certificate_id
            or not profile.certificate_id.private_key_id
        ):
            diagnostics.append({"code": "certification_signing_profile_missing"})
        diagnostics.extend(OfficialCertificationSetPreflight._envelope_matches_run(run))
        try:
            if run.envelope_file:
                SignatureService().verify_envelope_signature(
                    base64.b64decode(run.envelope_file)
                )
        except (UserError, ValidationError, ValueError, TypeError) as error:
            diagnostics.append(
                {"code": "envelope_signature_invalid", "detail": str(error)[:240]}
            )
        return diagnostics

    @staticmethod
    def _envelope_matches_run(run):
        if not run.envelope_file:
            return []
        try:
            envelope_bytes = base64.b64decode(run.envelope_file, validate=True)
            lines = run.line_ids.sorted("sequence")
            SiiEnvelopeComposer().validate(envelope_bytes, expected_count=len(lines))
            envelope_root = safe_fromstring(envelope_bytes)
            cover = envelope_root.xpath(
                "./*[local-name()='SetDTE']/*[local-name()='Caratula']"
            )
            if len(cover) != 1:
                raise UserError(_("The envelope is missing its SII cover."))
            cover = cover[0]
            set_record = run.set_id
            expected_cover = {
                "RutEmisor": run.company_id.partner_id.vat,
                "RutEnvia": set_record.sender_vat,
                "RutReceptor": "60803000-K",
                "NroResol": set_record.resolution_number,
                "FchResol": fields.Date.to_string(set_record.resolution_date),
            }
            for tag, expected_value in expected_cover.items():
                actual_value = cover.xpath(f"string(./*[local-name()='{tag}'])")
                if tag in {"RutEmisor", "RutEnvia", "RutReceptor"}:
                    caf_model = run.env["l10n_cl.caf.authorization"]
                    matches = caf_model._normalize_vat(
                        actual_value
                    ) == caf_model._normalize_vat(expected_value)
                else:
                    matches = actual_value == str(expected_value or "")
                if not matches:
                    raise UserError(
                        _("The SII envelope cover has an invalid %s.") % tag
                    )
            dtes = envelope_root.xpath(
                "./*[local-name()='SetDTE']/*[local-name()='DTE']"
            )
            for line, envelope_dte in zip(lines, dtes, strict=True):
                signed = CertificationSetExecutionService().validate_case_result(line)
                signed_root = safe_fromstring(signed)
                signed_doc = signed_root.xpath("./*[local-name()='Documento']")
                envelope_doc = envelope_dte.xpath("./*[local-name()='Documento']")
                if len(signed_doc) != 1 or len(envelope_doc) != 1:
                    raise UserError(_("The envelope has a missing Documento element."))
                if _without_xml_declaration(signed) not in envelope_bytes:
                    raise UserError(
                        _("The envelope content differs from case %s.")
                        % line.case_id.case_identifier
                    )
                signed_id = signed_doc[0].get("ID")
                envelope_id = envelope_doc[0].get("ID")
                signed_folio = signed_doc[0].xpath(
                    "string(./*[local-name()='Encabezado']/*[local-name()='IdDoc']/*[local-name()='Folio'])"
                )
                envelope_folio = envelope_doc[0].xpath(
                    "string(./*[local-name()='Encabezado']/*[local-name()='IdDoc']/*[local-name()='Folio'])"
                )
                signed_code = signed_doc[0].xpath(
                    "string(./*[local-name()='Encabezado']/*[local-name()='IdDoc']/*[local-name()='TipoDTE'])"
                )
                envelope_code = envelope_doc[0].xpath(
                    "string(./*[local-name()='Encabezado']/*[local-name()='IdDoc']/*[local-name()='TipoDTE'])"
                )
                if (
                    signed_id != envelope_id
                    or signed_folio != envelope_folio
                    or signed_code != envelope_code
                    or envelope_code != line.case_id.document_code
                ):
                    raise UserError(
                        _("The envelope DTE does not match case %s.")
                        % line.case_id.case_identifier
                    )
        except (UserError, ValidationError, ValueError, TypeError) as error:
            return [{"code": "envelope_content_invalid", "detail": str(error)[:240]}]
        return []

    @staticmethod
    def _coverage_digest(run):
        certification_set = run.set_id
        run_lines = run.line_ids.sorted("sequence")
        digest_payload = {
            "source_digest": certification_set.source_digest,
            "normalized_digest": certification_set.normalized_digest,
            "run_id": run.id,
            "revision": run.revision,
            "issue_date": fields.Date.to_string(run.issue_date),
            "cases": [
                {
                    "identifier": line.case_id.case_identifier,
                    "sequence": line.sequence,
                    "document_id": line.document_id.id,
                    "folio": line.document_id.folio,
                    "receiver_rut": line.receiver_rut_snapshot,
                    "xml_digest": line.document_id._get_signed_artifact().sha256
                    if line.document_id
                    else False,
                }
                for line in run_lines
            ],
            "envelope_digest": run.envelope_digest,
        }
        return CertificationSetParser().canonical_digest(digest_payload)
