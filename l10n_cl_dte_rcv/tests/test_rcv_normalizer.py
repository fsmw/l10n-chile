# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from ..services.rcv_normalizer import RcvNormalizer
from .common import COMPANY_RUT, CUSTOMER_RUT, RcvTestCase


def _summary(count, net=0, vat=0, total=0, type_code=33):
    return {
        "rsmnTipoDocInteger": type_code,
        "rsmnTotDoc": count,
        "rsmnMntNeto": net,
        "rsmnMntExe": 0,
        "rsmnMntIVA": vat,
        "rsmnMntTotal": total,
    }


def _detail(folio, rut=76222222, check_digit="1", net=100, vat=19):
    return {
        "detTipoDoc": 33,
        "detNroDoc": folio,
        "detFchDoc": "2026-08-15",
        "detRutDoc": rut,
        "detDvDoc": check_digit,
        "detRznSoc": "Fake Counterpart",
        "detMntNeto": net,
        "detMntExe": 0,
        "detMntIVA": vat,
        "detMntTotal": net + vat,
    }


@tagged("post_install", "-at_install")
class TestRcvNormalizer(RcvTestCase):
    def setUp(self):
        super().setUp()
        self.normalizer = RcvNormalizer(self.env)
        self.period = self._period()
        self.sync = self._sync(self.period)

    def _complete_section(self, state="REGISTRO"):
        return {
            "state": state,
            "summary": {"data": [_summary(1, 100, 19, 119)]},
            "details": {"33": [_detail(934)]},
        }

    def test_missing_detail_keeps_summary_and_marks_partial(self):
        result = self.normalizer.apply(
            self.sync,
            [
                {
                    "state": "PENDIENTE",
                    "unavailable_details": ["33"],
                    "summary": {"data": [_summary(2, 200, 38, 238)]},
                    "details": {"33": []},
                }
            ],
        )
        self.assertEqual(result, "partial")
        self.assertEqual(self.sync.summary_ids.document_count, 2)
        self.assertEqual(self.sync.detail_count, 0)
        self.assertEqual(self.sync.section_ids.state, "partial")
        self.assertTrue(self.sync.section_ids.error_message)
        self.assertTrue(self.sync.response_hash)

    def test_detail_totals_must_match_summary(self):
        section = self._complete_section()
        section["summary"]["data"][0]["rsmnMntNeto"] = 999
        self.assertEqual(self.normalizer.apply(self.sync, [section]), "partial")
        self.assertEqual(self.sync.section_ids.observed_document_count, 1)

    def test_fetch_error_marks_section_failed(self):
        section = self._complete_section()
        section["fetch_error"] = True
        self.assertEqual(self.normalizer.apply(self.sync, [section]), "partial")
        self.assertEqual(self.sync.section_ids.state, "failed")

    def test_complete_detail_is_idempotent_across_reads(self):
        payload = [self._complete_section()]
        self.assertEqual(self.normalizer.apply(self.sync, payload), "success")
        self.assertEqual(self.sync.detail_count, 1)
        self.assertEqual(self.sync.section_ids.state, "complete")
        document = self.sync.document_ids
        self.assertEqual(document.folio, "934")
        self.assertEqual(document.issuer_vat, "76222222-1")
        self.assertEqual(document.counterpart_vat, COMPANY_RUT)
        self.assertEqual(document.tax_period, "202608")
        self.assertEqual(document.total_amount, 119)
        self.assertFalse(document.tax_breakdown_available)

        next_sync = self._sync(self.period)
        self.assertEqual(self.normalizer.apply(next_sync, payload), "success")
        documents = self.env["l10n_cl.dte.rcv.document"].search(
            [("company_id", "=", self.company.id), ("folio", "=", "934")]
        )
        self.assertEqual(documents, document)
        self.assertEqual(document.last_sync_id, next_sync)
        self.assertEqual(len(document.observation_ids), 2)

    def test_sale_documents_are_issued_by_the_company(self):
        period = self._period(operation="sale")
        sync = self._sync(period)
        section = self._complete_section()
        section["details"]["33"] = [_detail(55, rut=12345678, check_digit="5")]
        self.assertEqual(self.normalizer.apply(sync, [section]), "success")
        document = sync.document_ids
        self.assertEqual(document.issuer_vat, COMPANY_RUT)
        self.assertEqual(document.issuer_name, self.company.name)
        self.assertEqual(document.counterpart_vat, CUSTOMER_RUT)
        self.assertEqual(document.counterpart_name, "Fake Counterpart")

    def test_archived_document_is_updated_not_duplicated(self):
        self.normalizer.apply(self.sync, [self._complete_section()])
        document = self.sync.document_ids
        document.active = False
        next_sync = self._sync(self.period)
        self.assertEqual(
            self.normalizer.apply(next_sync, [self._complete_section()]), "success"
        )
        self.assertEqual(document.last_sync_id, next_sync)
        self.assertFalse(document.active)

    def test_failed_section_rolls_back_only_itself(self):
        result = self.normalizer.apply(
            self.sync,
            [
                {
                    "state": "REGISTRO",
                    "summary": {"data": [_summary(0)]},
                    "details": {"33": []},
                },
                {
                    "state": "PENDIENTE",
                    "summary": {"data": [_summary(1)]},
                    "details": {"33": 1},
                },
                "not a section",
            ],
        )
        self.assertEqual(result, "partial")
        self.assertEqual(self.sync.summary_ids.mapped("sii_state"), ["REGISTRO"])
        states = {section.sii_state: section.state for section in self.sync.section_ids}
        self.assertEqual(
            states, {"REGISTRO": "complete", "PENDIENTE": "failed", "UNKNOWN": "failed"}
        )

    def test_row_without_folio_marks_section_partial(self):
        section = self._complete_section()
        section["details"]["33"] = [_detail("")]
        self.assertEqual(self.normalizer.apply(self.sync, [section]), "partial")
        self.assertFalse(self.sync.document_ids)
