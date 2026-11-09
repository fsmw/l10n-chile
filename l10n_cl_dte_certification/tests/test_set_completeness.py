# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from types import SimpleNamespace

from odoo.tests import TransactionCase

from ..services.set_completeness_service import CertificationSetCompletenessService


class TestCertificationSetCompleteness(TransactionCase):
    """Test source-aware certification set review and safe completion."""

    def test_analysis_has_no_fiscal_side_effects(self):
        certification_set = self._fake_set()
        before = certification_set.case_ids[0].partner_id
        service = CertificationSetCompletenessService()
        first = service.analyze_set(certification_set, "official_sii")
        second = service.analyze_set(certification_set, "official_sii")
        self.assertEqual(first, second)
        self.assertIs(certification_set.case_ids[0].partner_id, before)

    def test_official_mode_never_suggests_synthetic_fiscal_values(self):
        result = CertificationSetCompletenessService().analyze_set(
            self._fake_set(), "official_sii"
        )
        findings = [finding for item in result for finding in item["findings"]]
        self.assertFalse(any(finding.get("synthetic") for finding in findings))
        self.assertFalse(
            any(
                finding["field"] in {"partner_id", "vat_tax_id", "document_code"}
                and finding.get("proposed_value")
                for finding in findings
            )
        )

    def test_local_mode_proposes_versioned_text_profile(self):
        result = CertificationSetCompletenessService().analyze_set(
            self._fake_set(note=True), "local_synthetic"
        )
        findings = [finding for item in result for finding in item["findings"]]
        proposal = next(
            item
            for item in findings
            if item["code"] == "missing_text_correction_content"
        )
        self.assertEqual(proposal["source"], "synthetic_local")
        self.assertEqual(proposal["profile"], "note_text_v1")

    def test_synthetic_completion_blocks_official_preflight(self):
        from ..services.official_set_preflight import OfficialCertificationSetPreflight

        diagnostic = (
            OfficialCertificationSetPreflight._synthetic_completion_diagnostics(
                SimpleNamespace(contains_synthetic_completion=True)
            )
        )
        self.assertEqual(diagnostic, [{"code": "set_contains_synthetic_completion"}])

    def test_review_digest_is_stable(self):
        service = CertificationSetCompletenessService()
        result = service.analyze_set(self._fake_set(), "official_sii")
        self.assertEqual(
            service.build_review_digest(result, "official_sii"),
            service.build_review_digest(result, "official_sii"),
        )

    @staticmethod
    def _fake_set(note=False):
        source_case = {
            "identifier": "case-1",
            "document_code": "61" if note else "33",
            "lines": [],
            "unsupported_instructions": [],
        }
        case = SimpleNamespace(
            id=1,
            sequence=1,
            case_identifier="case-1",
            document_code=source_case["document_code"],
            partner_id=False,
            global_discount_percent=False,
            note_operation="text" if note else False,
            reference_case_identifier=False,
            reference_reason=False,
            sii_reference_reason=False,
            line_ids=[],
            diagnostics=[],
        )
        certification_set = SimpleNamespace(
            company_id=SimpleNamespace(vat=""),
            journal_id=False,
            vat_tax_id=False,
            resolution_number=False,
            resolution_date=False,
            sender_vat=False,
            normalized_payload={"cases": [source_case]},
            case_ids=[case],
        )
        case.set_id = certification_set
        return certification_set
