# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError
from odoo.tests import tagged

from ..services.rcv_normalizer import RcvNormalizer
from .common import RcvTestCase

RCV_MODELS = (
    "l10n_cl.dte.rcv.period",
    "l10n_cl.dte.rcv.sync",
    "l10n_cl.dte.rcv.section",
    "l10n_cl.dte.rcv.summary",
    "l10n_cl.dte.rcv.document",
    "l10n_cl.dte.rcv.observation",
    "l10n_cl.dte.rcv.boleta.daily",
)


@tagged("post_install", "-at_install")
class TestRcvSecurity(RcvTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.other_company = cls.env["res.company"].create(
            {"name": "RCV Test Company B", "vat": "76543210-3"}
        )

    def _store(self, company):
        period = self._period(company=company)
        sync = self._sync(period)
        RcvNormalizer(self.env).apply(
            sync,
            [
                {
                    "state": "REGISTRO",
                    "summary": {"data": [{"rsmnTipoDocInteger": 33, "rsmnTotDoc": 1}]},
                    "details": {
                        "33": [{"detNroDoc": 1, "detRutDoc": 76222222, "detDvDoc": "1"}]
                    },
                }
            ],
        )
        self.env["l10n_cl.dte.rcv.boleta.daily"].create(
            {"sync_id": sync.id, "day": "2026-08-01", "document_type_code": "39"}
        )
        return sync

    def test_users_only_see_allowed_companies(self):
        self._store(self.company)
        self._store(self.other_company)
        for model in RCV_MODELS:
            records = self.env[model].with_user(self.reader).search([])
            self.assertTrue(records, model)
            self.assertEqual(records.mapped("company_id"), self.company, model)

    def test_reader_can_read_but_not_write(self):
        sync = self._store(self.company)
        for model in RCV_MODELS:
            self.env[model].with_user(self.reader).check_access("read")
            with self.assertRaises(AccessError, msg=model):
                self.env[model].with_user(self.reader).check_access("write")
        self.assertEqual(sync.with_user(self.reader).document_ids.folio, "1")

    def test_manager_can_write_but_not_delete(self):
        for model in RCV_MODELS:
            self.env[model].with_user(self.manager).check_access("create")
            with self.assertRaises(AccessError, msg=model):
                self.env[model].with_user(self.manager).check_access("unlink")
