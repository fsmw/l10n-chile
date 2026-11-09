# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hashlib
import json
from decimal import Decimal, InvalidOperation

from odoo import _

from .receiver_activity_correction import (
    is_receiver_activity_correction,
    unresolved_source_instructions,
)
from .receiver_assignment_service import CertificationReceiverAssignmentService
from .sii_activity_catalogue import PROFILE
from .synthetic_receiver_activity_service import SyntheticReceiverActivityService


class CertificationSetCompletenessService:
    """Analyze certification case readiness without causing fiscal side effects."""

    def analyze_set(self, certification_set, target_mode):
        """Return stable, source-aware diagnostics and safe proposals for a set."""
        if target_mode not in {"official_sii", "local_synthetic"}:
            raise ValueError("Unknown certification review mode.")
        results = [
            self._analyze_case(case, certification_set.normalized_payload, target_mode)
            for case in sorted(
                certification_set.case_ids, key=lambda item: item.sequence
            )
        ]
        if getattr(certification_set, "env", None):
            by_id = {item["case_id"]: item for item in results}
            caf_model = certification_set.env["l10n_cl.caf.authorization"]
            used_ruts = set()
            for case in certification_set.case_ids.sorted("sequence"):
                if not case.partner_id:
                    continue
                findings = by_id[case.id]["findings"]
                for message in CertificationReceiverAssignmentService.receiver_problems(
                    case.partner_id, certification_set
                ):
                    findings.append(
                        _finding(
                            "invalid_receiver_data",
                            "partner_id",
                            message,
                            "existing_odoo_record",
                        )
                    )
                if case.document_code in {"33", "34"}:
                    rut = caf_model._normalize_vat(case.partner_id.vat)
                    if rut in used_ruts:
                        findings.append(
                            _finding(
                                "duplicate_invoice_receiver",
                                "partner_id",
                                _("Select a different receiver RUT for each invoice."),
                                "existing_odoo_record",
                            )
                        )
                    used_ruts.add(rut)
        for code, field in self._configuration_findings(certification_set):
            message = "Required company or set configuration is missing."
            if code == "missing_issuer_address":
                message = certification_set.env._(
                    "Complete the issuer address in Set Configuration."
                )
            elif code == "missing_issuer_commune":
                message = certification_set.env._(
                    "Complete the issuer commune in Set Configuration."
                )
            elif code == "certification_signing_profile_missing":
                message = certification_set.env._(
                    "Configure the Certification certificate in Set Configuration."
                )
            elif code == "missing_sii_resolution":
                message = _(
                    "Enter the resolution date published for this company "
                    "in the SII Certification registry."
                )
            results.append(
                {
                    "case_id": False,
                    "case_identifier": False,
                    "document_code": False,
                    "findings": [
                        _finding(
                            code,
                            field,
                            message,
                            "company_configuration",
                        )
                    ],
                }
            )
        if getattr(certification_set, "env", None):
            for item in certification_set._get_folio_requirements():
                code = item["document_code"]
                if not item["missing"] or not certification_set._cafs_for_document_code(
                    code
                ):
                    continue  # Missing CAFs already have a configuration finding.
                results.append(
                    {
                        "case_id": False,
                        "case_identifier": False,
                        "document_code": code,
                        "findings": [
                            _finding(
                                f"insufficient_caf_folios_{code}",
                                "caf_line_ids",
                                item["message"],
                                "company_configuration",
                            )
                        ],
                    }
                )
        return results

    @staticmethod
    def _configuration_findings(certification_set):
        checks = (
            (
                not certification_set.company_id.vat,
                "missing_company_tax_id",
                "company_id",
            ),
            (not certification_set.journal_id, "missing_sales_journal", "journal_id"),
            (not certification_set.vat_tax_id, "missing_vat_tax", "vat_tax_id"),
            (
                not certification_set.resolution_number
                or not certification_set.resolution_date,
                "missing_sii_resolution",
                "resolution_date",
            ),
            (not certification_set.sender_vat, "missing_sender_tax_id", "sender_vat"),
        )
        findings = [(code, field) for failed, code, field in checks if failed]
        document_codes = {
            case.document_code
            for case in certification_set.case_ids
            if case.document_code in {"33", "34", "56", "61"}
        }
        for code in sorted(document_codes):
            if hasattr(
                certification_set, "_cafs_for_document_code"
            ) and not certification_set._cafs_for_document_code(code):
                findings.append((f"missing_caf_{code}", f"caf_{code}_id"))
        env = getattr(certification_set, "env", None)
        if env:
            issuer = certification_set.company_id.partner_id
            if not (issuer.street or issuer.street2 or "").strip():
                findings.append(("missing_issuer_address", "issuer_street"))
            if not (issuer.city or "").strip():
                findings.append(("missing_issuer_commune", "issuer_commune"))
            profiles = env["l10n_cl.dte.signing.profile"].search(
                [
                    ("company_id", "=", certification_set.company_id.id),
                    ("sii_environment", "=", "certification"),
                    ("active", "=", True),
                ]
            )
            if (
                len(profiles) != 1
                or not profiles.certificate_id
                or not profiles.certificate_id.private_key_id
                or not profiles.certificate_id.active
                or not profiles.certificate_id.is_valid
            ):
                findings.append(
                    ("certification_signing_profile_missing", "signing_profile")
                )
        return findings

    @staticmethod
    def _analyze_case(case, normalized_payload, target_mode):
        source_case = next(
            (
                item
                for item in normalized_payload.get("cases", [])
                if item.get("identifier") == case.case_identifier
            ),
            {},
        )
        findings = []
        if not source_case:
            findings.append(
                _finding(
                    "source_case_missing",
                    "normalized_payload",
                    "Case is absent from the normalized source.",
                )
            )
        else:
            findings.extend(_source_content_findings(case, source_case))
            findings.extend(_source_and_receiver_findings(case, source_case))
            findings.extend(_detail_findings(case, source_case))
            if case.document_code in {"56", "61"}:
                findings.extend(_note_findings(case, target_mode))
        # Persisted diagnostics are read-only inputs: readiness validation
        # writes fields and is deliberately not called from this analyzer.
        for code in case.diagnostics or []:
            if isinstance(code, str) and not any(
                item["code"] == code for item in findings
            ):
                findings.append(
                    _finding(code, "case", "Resolve this case readiness diagnostic.")
                )
        return {
            "case_id": case.id,
            "case_identifier": case.case_identifier,
            "document_code": case.document_code,
            "findings": findings,
        }

    def build_review_digest(self, analysis, target_mode):
        """Return a canonical digest for the review decisions and proposals."""
        canonical = json.dumps(
            {"mode": target_mode, "analysis": analysis},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _finding(code, field, message, source="sii_source"):
    return {
        "code": code,
        "field": field,
        "message": message,
        "source": source,
        "severity": "blocking",
        "synthetic": False,
        "proposed_value": False,
    }


def _source_and_receiver_findings(case, source_case):
    findings = []
    if not source_case.get("document_code"):
        findings.append(
            _finding(
                "source_incomplete_document_code",
                "document_code",
                "The source does not identify a DTE type.",
            )
        )
    if not case.partner_id:
        findings.append(
            _finding(
                "missing_receiver",
                "partner_id",
                "Choose the real test receiver in Odoo before generating this "
                "document. The SII TXT does not include receiver details.",
                "existing_odoo_record",
            )
        )
    elif not case.partner_id.vat:
        findings.append(
            _finding(
                "missing_receiver_tax_id",
                "partner_id",
                "The mapped receiver has no tax ID.",
                "existing_odoo_record",
            )
        )
    return findings


def _source_content_findings(case, source_case):
    findings = []
    if case.document_code not in {"33", "34", "56", "61"}:
        findings.append(
            _finding(
                "unsupported_document_type",
                "document_code",
                "This DTE type is not supported by the certification flow.",
            )
        )
    if unresolved_source_instructions(case, source_case):
        findings.append(
            _finding(
                "unsupported_instructions",
                "instruction_text",
                "The source contains instructions that need a reviewed interpretation.",
            )
        )
    discount = case.global_discount_percent
    if discount:
        try:
            parsed_discount = Decimal(discount)
            valid_discount = case.document_code == "33" and Decimal(
                0
            ) < parsed_discount <= Decimal(100)
        except (InvalidOperation, TypeError, ValueError):
            valid_discount = False
        if not valid_discount:
            findings.append(
                _finding(
                    "invalid_global_discount",
                    "global_discount_percent",
                    "The global discount is invalid for this DTE type.",
                )
            )
    return findings


def _detail_findings(case, source_case):
    findings = []
    source_lines = source_case.get("lines", [])
    if len(case.line_ids) != len(source_lines) and not (
        case.document_code in {"56", "61"} and case.note_operation in {"cancel", "text"}
    ):
        findings.append(
            _finding(
                "detail_count_mismatch",
                "line_ids",
                "Mapped details do not match the source detail count.",
            )
        )
    if not case.line_ids and not (
        case.document_code in {"56", "61"} and case.note_operation in {"cancel", "text"}
    ):
        findings.append(
            _finding(
                "missing_details",
                "line_ids",
                "The case has no mapped detail lines.",
                "existing_odoo_record",
            )
        )
    for line, source_line in zip(
        sorted(case.line_ids, key=lambda item: item.sequence),
        source_lines,
        strict=False,
    ):
        if (
            line.name != source_line.get("name")
            or (line.quantity_text or None) != source_line.get("quantity")
            or (line.unit_price_text or None) != source_line.get("unit_price")
        ):
            findings.append(
                _finding(
                    "detail_differs_from_source",
                    "line_ids",
                    "A mapped detail differs from its original source values.",
                )
            )
        if not line.product_id and not case.set_id.journal_id.default_account_id:
            finding = _finding(
                "missing_product_mapping",
                "product_id",
                "Select a matching Odoo product for this detail, or configure "
                "a default income account on the certification sales journal.",
                "existing_odoo_record",
            )
            finding["case_line_id"] = line.id
            findings.append(finding)
        if not line.quantity_text or (
            not line.unit_price_text and not _inherits_return_price(case, line)
        ):
            findings.append(
                _finding(
                    "source_incomplete_detail",
                    "line_ids",
                    "The source is missing a fiscal detail value.",
                )
            )
        if case.document_code == "34" and not line.exempt:
            findings.append(
                _finding(
                    "invoice_34_requires_exempt_lines",
                    "line_ids",
                    "All type 34 lines must be exempt.",
                )
            )
    return findings


def _inherits_return_price(case, line):
    """A return reuses the unique original invoice item's price."""
    if case.document_code != "61" or case.note_operation != "return":
        return False
    matches = [
        original
        for target in case.set_id.case_ids
        if target.case_identifier == case.reference_case_identifier
        and target.sequence < case.sequence
        and target.document_code in {"33", "34"}
        for original in target.line_ids
        if original.name.strip() == line.name.strip()
    ]
    return len(matches) == 1 and bool(matches[0].unit_price_text)


def _note_findings(case, target_mode):
    findings = []
    if not case.reference_case_identifier:
        findings.append(
            _finding(
                "source_incomplete_note_reference",
                "reference_case_identifier",
                "The source does not identify the referenced case.",
            )
        )
    if not case.note_operation:
        findings.append(
            _finding(
                "source_incomplete_note_operation",
                "note_operation",
                "The source does not identify the note operation.",
            )
        )
    if not case.reference_reason:
        findings.append(
            _finding(
                "incomplete_note_reference",
                "reference_reason",
                "The source does not provide a note reference reason.",
            )
        )
    targets = [
        item
        for item in case.set_id.case_ids
        if item.case_identifier == case.reference_case_identifier
    ]
    expected_target_types = {"61"} if case.document_code == "56" else {"33", "34"}
    operation_matches_type = (
        case.document_code == "56"
        and case.note_operation == "cancel"
        and len(targets) == 1
        and targets[0].note_operation == "text"
        or case.document_code == "61"
        and case.note_operation in {"text", "return", "cancel"}
    )
    if (
        len(targets) != 1
        or targets[0].sequence >= case.sequence
        or targets[0].document_code not in expected_target_types
        or not operation_matches_type
    ):
        findings.append(
            _finding(
                "invalid_note_reference_case",
                "reference_case_identifier",
                "The note must refer to an earlier supported DTE case.",
            )
        )
    elif case.partner_id and targets[0].partner_id != case.partner_id:
        findings.append(
            _finding(
                "note_receiver_differs_from_reference",
                "partner_id",
                "The note receiver must match the referenced case.",
            )
        )
    if case.note_operation == "text" and not case.sii_reference_reason:
        finding = _finding(
            "missing_text_correction_content",
            "sii_reference_reason",
            "The exact text correction must come from an authorized source.",
        )
        if target_mode == "local_synthetic":
            activity_text = (
                SyntheticReceiverActivityService().proposed_text(case)
                if is_receiver_activity_correction(case)
                else None
            )
            finding.update(
                {
                    "source": "synthetic_local",
                    "synthetic": True,
                    "profile": PROFILE if activity_text else "note_text_v1",
                    "proposed_value": activity_text
                    or (
                        "Dice: Texto original de prueba. "
                        "Debe decir: Texto corregido de prueba."
                    ),
                }
            )
        findings.append(finding)
    return findings
