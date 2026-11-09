# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_account.services.dte_issuance_adapter import (
    InvoiceIssuanceAdapter,
)
from odoo.addons.l10n_cl_dte_certification.models.certification_case import (
    DteCertificationCase,
)
from odoo.addons.l10n_cl_dte_certification.models.certification_set import (
    DteCertificationSet,
)
from odoo.addons.l10n_cl_dte_certification.services.case_generation_service import (
    CertificationCaseGenerationService,
)
from odoo.addons.l10n_cl_dte_certification.services.official_set_preflight import (
    OfficialCertificationSetPreflight,
)

from ..services.certification_invoice_adapter import CertificationInvoiceAdapter
from .common import DteCertificationTestCase


class _OrderedRecords(list):
    def sorted(self, key):
        return _OrderedRecords(sorted(self, key=lambda item: getattr(item, key)))

    def filtered(self, predicate):
        if isinstance(predicate, str):
            field_name = predicate

            def predicate(item):
                return getattr(item, field_name)

        return _OrderedRecords(item for item in self if predicate(item))

    def __getattr__(self, name):
        if len(self) == 1:
            return getattr(self[0], name)
        raise AttributeError(name)


@tagged("post_install", "-at_install")
class TestDteCertificationSetPreflight(DteCertificationTestCase):
    def test_global_discount_leaves_exempt_items_and_item_discounts_unchanged(self):
        details = _OrderedRecords(
            SimpleNamespace(
                sequence=index,
                name=f"ITEM {index}",
                quantity_text="2",
                unit_price_text="6840",
                discount_percent_text=discount,
                exempt=exempt,
                product_id=False,
            )
            for index, exempt, discount in [
                (1, False, "0"),
                (2, True, "0"),
                (3, True, "10"),
            ]
        )
        case = SimpleNamespace(
            document_code="33", global_discount_percent="24", line_ids=details
        )
        result = CertificationCaseGenerationService()._resolved_details(case)
        self.assertEqual([d["discount_percent"] for d in result], ["24", "0", "10"])

    def test_global_discount_snapshot_keeps_exempt_line_subtotal(self):
        case = SimpleNamespace(
            id=1, case_identifier="TEST-4", global_discount_percent="24"
        )
        run = SimpleNamespace(
            set_id=SimpleNamespace(id=1, source_digest="source"),
            revision=1,
            input_digest="input",
            issue_date=fields.Date.from_string("2026-11-05"),
        )
        line = SimpleNamespace(case_id=case, run_id=run)
        move = SimpleNamespace(
            invoice_line_ids=_OrderedRecords(
                [
                    SimpleNamespace(
                        display_type="product", quantity=450, price_unit=6379
                    ),
                    SimpleNamespace(
                        display_type="product", quantity=2, price_unit=6840
                    ),
                ]
            )
        )
        snapshot = {
            "lines": [
                {"exempt": False, "subtotal": "2181618", "discount_percent": "24"},
                {"exempt": True, "subtotal": "12312", "discount_percent": "10"},
            ]
        }
        with (
            patch.object(CertificationInvoiceAdapter, "_check_case", return_value=line),
            patch.object(
                InvoiceIssuanceAdapter, "capture_snapshot", return_value=snapshot
            ),
            patch(
                "odoo.addons.l10n_cl_dte_certification.services.certification_invoice_adapter.apply_receiver_activity_to_snapshot",
                side_effect=lambda value, _case: value,
            ),
        ):
            result = CertificationInvoiceAdapter().capture_snapshot(move, None, 1, "33")
        self.assertEqual(result["lines"][0]["subtotal"], "2870550")
        self.assertIsNone(result["lines"][0]["discount_percent"])
        self.assertEqual(result["lines"][1]["subtotal"], "12312")
        self.assertEqual(result["lines"][1]["discount_percent"], "10")
        self.assertEqual(result["global_discount"]["value"], "24")

    def _run(self, *, partial=False, reverse=False):
        case_a = SimpleNamespace(id=1, sequence=10, case_identifier="SET-CASO-1")
        case_b = SimpleNamespace(id=2, sequence=20, case_identifier="SET-CASO-2")
        cases = _OrderedRecords([case_a, case_b])
        lines = [
            SimpleNamespace(
                case_id=case_a,
                sequence=10,
                selected=True,
                state="prepared",
                document_id=object(),
            ),
            SimpleNamespace(
                case_id=case_b,
                sequence=20,
                selected=True,
                state="prepared",
                document_id=object(),
            ),
        ]
        if reverse:
            lines[0].case_id, lines[1].case_id = lines[1].case_id, lines[0].case_id
        certification_set = SimpleNamespace(
            case_ids=cases,
            _check_certification_access=MagicMock(),
            _verify_source_integrity=MagicMock(),
        )
        run = SimpleNamespace(
            env=self.env,
            _context={},
            set_id=certification_set,
            state="exported",
            is_partial=partial,
            line_ids=_OrderedRecords(lines),
            issue_date=fields.Date.today(),
            envelope_file=False,
            envelope_digest=False,
        )
        return run

    def test_type_56_may_cancel_a_type_61_reference_from_official_set(self):
        target = SimpleNamespace(
            case_identifier="SET-61",
            sequence=1,
            document_code="61",
            partner_id=False,
            note_operation="text",
        )
        case_set = SimpleNamespace(case_ids=_OrderedRecords([target]))
        case = SimpleNamespace(
            set_id=case_set,
            document_code="56",
            sequence=2,
            reference_case_identifier="SET-61",
            partner_id=False,
            note_operation="cancel",
            reference_reason="ANULA NOTA DE CREDITO",
        )

        self.assertEqual(DteCertificationCase._note_readiness_problems(case), [])

    def test_optional_note_details_match_source_when_empty_values_are_false(self):
        line = SimpleNamespace(
            sequence=1,
            name="ITEM DE PRUEBA",
            quantity_text="1",
            unit_price_text=False,
            exempt=False,
            _check_decimal_values=MagicMock(),
        )
        case = SimpleNamespace(
            ensure_one=MagicMock(),
            document_code="56",
            note_operation="return",
            line_ids=_OrderedRecords([line]),
        )
        source_case = {
            "lines": [
                {
                    "name": "ITEM DE PRUEBA",
                    "quantity": "1",
                    "unit_price": None,
                }
            ]
        }

        self.assertEqual(
            DteCertificationCase._detail_readiness_problems(case, source_case), []
        )

    def test_note_draft_is_deferred_until_its_target_dte_is_prepared(self):
        invoice_line = SimpleNamespace(
            case_id=SimpleNamespace(document_code="33"),
            move_id=object(),
            selected=True,
            sequence=1,
        )
        note_line = SimpleNamespace(
            case_id=SimpleNamespace(document_code="56"),
            move_id=False,
            selected=True,
            sequence=2,
            _write_internal=MagicMock(),
        )
        run = SimpleNamespace(
            id=1,
            ensure_one=MagicMock(),
            set_id=SimpleNamespace(
                id=1,
                state="ready",
                _check_certification_access=MagicMock(),
            ),
            env=SimpleNamespace(cr=SimpleNamespace(execute=MagicMock())),
            state="draft",
            input_digest=False,
            input_snapshot=False,
            line_ids=_OrderedRecords([invoice_line, note_line]),
            invalidate_recordset=MagicMock(),
            _write_internal=MagicMock(),
        )
        run.line_ids.invalidate_recordset = MagicMock()

        with (
            patch.object(
                CertificationCaseGenerationService, "validate_run", return_value={}
            ),
            patch.object(
                CertificationCaseGenerationService, "_freeze_input", return_value={}
            ),
        ):
            CertificationCaseGenerationService().generate(run)

        note_line._write_internal.assert_called_once_with(
            {
                "state": "pending",
                "diagnostic_code": False,
                "diagnostic_message": False,
            }
        )
        self.assertEqual(run._write_internal.call_args.args[0]["state"], "generated")

    def test_official_submission_requires_every_case_in_original_order(self):
        diagnostics = OfficialCertificationSetPreflight._coverage_diagnostics(
            self._run(reverse=True)
        )
        codes = {item["code"] for item in diagnostics}
        self.assertIn("case_order_mismatch", codes)

    def test_receiver_requires_no_attestation_but_invoice_ruts_must_differ(self):
        receiver = SimpleNamespace(vat="12345678-5")
        case = SimpleNamespace(
            partner_id=receiver,
            case_identifier="SET-CASO-1",
            document_code="33",
            set_id=SimpleNamespace(case_ids=[]),
        )
        caf_model = MagicMock()
        caf_model._normalize_vat.return_value = "12345678-5"
        caf_model._is_valid_vat.return_value = True
        line = SimpleNamespace(
            case_id=case,
            receiver_partner_id=receiver,
            receiver_rut_snapshot="12345678-5",
            env={"l10n_cl.caf.authorization": caf_model},
        )
        seen = set()
        caf_model = MagicMock()
        caf_model._normalize_vat.return_value = "12345678-5"
        caf_model._is_valid_vat.return_value = True
        diagnostics = OfficialCertificationSetPreflight._receiver_diagnostics(
            line, caf_model, seen
        )
        self.assertEqual(diagnostics, [])
        duplicate_diagnostics = OfficialCertificationSetPreflight._receiver_diagnostics(
            line, caf_model, seen
        )
        self.assertIn(
            "receiver_rut_not_unique", {d["code"] for d in duplicate_diagnostics}
        )

    def test_note_document_types_are_registered_for_local_preflight(self):
        line = SimpleNamespace(
            case_id=SimpleNamespace(document_code="56", case_identifier="SET-CASO-1"),
            document_id=False,
            move_id=False,
        )
        diagnostics = OfficialCertificationSetPreflight()._line_diagnostics(
            line, MagicMock(), set(), MagicMock()
        )
        self.assertEqual(diagnostics, [])

    def _caf_line(self, signature_status="key_unavailable"):
        company = object()
        caf = SimpleNamespace(
            company_id=company,
            document_code="33",
            sii_environment="certification",
            is_synthetic=False,
            state="validated",
            sii_signature_status=signature_status,
            folio_start=10,
            folio_end=20,
            _check_caf_integrity=MagicMock(),
        )
        certification_set = SimpleNamespace(
            caf_line_ids=_OrderedRecords(
                [SimpleNamespace(caf_id=caf, document_code="33")]
            ),
            _caf_for_document_code=lambda code, folio=None: (
                caf
                if code == "33"
                and (folio is None or caf.folio_start <= folio <= caf.folio_end)
                else False
            ),
        )
        run = SimpleNamespace(set_id=certification_set)
        case = SimpleNamespace(document_code="33", case_identifier="SET-CASO-1")
        line = SimpleNamespace(
            company_id=company,
            case_id=case,
            run_id=run,
            document_id=SimpleNamespace(folio=15),
        )
        return line, caf

    def test_missing_sii_trust_key_warns_without_blocking_caf(self):
        line, caf = self._caf_line()

        self.assertEqual(OfficialCertificationSetPreflight._caf_diagnostics(line), [])
        self.assertEqual(
            OfficialCertificationSetPreflight._caf_warning(line),
            {"code": "caf_frma_not_verified", "case": "SET-CASO-1"},
        )
        caf._check_caf_integrity.assert_called_once_with()

    def test_corrupt_caf_digest_still_blocks_preflight(self):
        line, caf = self._caf_line(signature_status="verified")
        caf._check_caf_integrity.side_effect = ValidationError("digest mismatch")

        diagnostics = OfficialCertificationSetPreflight._caf_diagnostics(line)

        self.assertEqual(diagnostics[0]["code"], "caf_folio_or_frma_invalid")

    def test_partial_execution_is_not_reportable(self):
        diagnostics = OfficialCertificationSetPreflight._coverage_diagnostics(
            self._run(partial=True)
        )
        self.assertIn("partial_execution", {item["code"] for item in diagnostics})

    def test_generation_preflight_reports_all_note_folio_shortages(self):
        run = SimpleNamespace(
            env=SimpleNamespace(
                cr=SimpleNamespace(
                    execute=MagicMock(),
                    fetchone=MagicMock(side_effect=[(1,), (0,), (11,), (0,)]),
                )
            ),
            company_id=SimpleNamespace(id=7),
        )
        cafs = {
            "56": [
                SimpleNamespace(
                    id=1, folio_start=8, folio_end=12, state="validated", date_to=False
                ),
                SimpleNamespace(
                    id=2, folio_start=13, folio_end=13, state="validated", date_to=False
                ),
            ],
            "61": [
                SimpleNamespace(
                    id=3, folio_start=13, folio_end=24, state="validated", date_to=False
                ),
                SimpleNamespace(
                    id=4, folio_start=25, folio_end=25, state="validated", date_to=False
                ),
            ],
        }
        certification_set = SimpleNamespace(
            _cafs_for_document_code=lambda code: cafs[code]
        )

        with self.assertRaises(UserError) as error:
            CertificationCaseGenerationService._validate_folio_capacity(
                run, certification_set, {"56": 6, "61": 3}
            )

        message = str(error.exception)
        self.assertIn("DTE 56: 5 available, 6 needed", message)
        self.assertIn("DTE 61: 2 available, 3 needed", message)
        self.assertEqual(run.env.cr.execute.call_count, 4)
        self.assertTrue(
            all(
                call.args[0].lstrip().startswith("SELECT COUNT(*)")
                for call in run.env.cr.execute.call_args_list
            )
        )
        self.assertEqual(
            [call.args[1] for call in run.env.cr.execute.call_args_list],
            [[1], [2], [3], [4]],
        )

    def test_generation_preflight_accepts_combined_caf_capacity(self):
        run = SimpleNamespace(
            env=SimpleNamespace(
                cr=SimpleNamespace(
                    execute=MagicMock(),
                    fetchone=MagicMock(side_effect=[(0,), (0,), (0,), (0,)]),
                )
            ),
            company_id=SimpleNamespace(id=7),
        )
        cafs = {
            "56": [
                SimpleNamespace(
                    id=1, folio_start=8, folio_end=12, state="validated", date_to=False
                ),
                SimpleNamespace(
                    id=2, folio_start=13, folio_end=13, state="validated", date_to=False
                ),
            ],
            "61": [
                SimpleNamespace(
                    id=3, folio_start=13, folio_end=24, state="validated", date_to=False
                ),
                SimpleNamespace(
                    id=4, folio_start=25, folio_end=25, state="validated", date_to=False
                ),
            ],
        }
        certification_set = SimpleNamespace(
            _cafs_for_document_code=lambda code: cafs[code]
        )

        CertificationCaseGenerationService._validate_folio_capacity(
            run, certification_set, {"56": 6, "61": 3}
        )

        self.assertEqual(run.env.cr.execute.call_count, 4)

    def test_caf_selection_falls_through_to_next_available_range(self):
        exhausted = SimpleNamespace(
            id=1,
            state="validated",
            date_to=False,
            folio_start=8,
            folio_end=12,
            _lock_range=MagicMock(),
        )
        additional = SimpleNamespace(
            id=2,
            state="validated",
            date_to=False,
            folio_start=13,
            folio_end=13,
            _lock_range=MagicMock(),
        )
        fake_set = SimpleNamespace(
            ensure_one=MagicMock(),
            _cafs_for_document_code=MagicMock(return_value=[exhausted, additional]),
            env=SimpleNamespace(
                cr=SimpleNamespace(
                    execute=MagicMock(), fetchone=MagicMock(side_effect=[(5,), (0,)])
                )
            ),
        )

        selected = DteCertificationSet._caf_with_available_folio(fake_set, "56")

        self.assertIs(selected, additional)
        self.assertEqual(fake_set.env.cr.execute.call_count, 2)
        exhausted._lock_range.assert_called_once_with()
        additional._lock_range.assert_called_once_with()
