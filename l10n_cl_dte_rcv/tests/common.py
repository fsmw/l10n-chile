# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo.tests import TransactionCase

# Synthetic RUTs with valid check digits; they do not identify anyone.
COMPANY_RUT = "76111111-6"
SUPPLIER_RUT = "76222222-1"
OTHER_SUPPLIER_RUT = "77333333-5"
CUSTOMER_RUT = "12345678-5"
CSV_HEADER = (
    "tipo_documento,rut_contraparte,razon_social,folio,fecha,"
    "neto,exento,iva,iva_no_recuperable,iva_uso_comun,total"
)


class RcvTestCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.vat = COMPANY_RUT
        cls.group_user = cls.env.ref("l10n_cl_dte_core.group_dte_user")
        cls.group_manager = cls.env.ref("l10n_cl_dte_core.group_dte_manager")
        cls.manager = cls._user("rcv-test-manager", cls.group_manager)
        cls.reader = cls._user("rcv-test-reader", cls.group_user)

    @classmethod
    def _user(cls, login, group, companies=None):
        companies = companies or cls.company
        return cls.env["res.users"].create(
            {
                "name": login,
                "login": login,
                "company_id": companies[:1].id,
                "company_ids": [(6, 0, companies.ids)],
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id, group.id])],
            }
        )

    def _period(self, operation="purchase", year=2026, month=8, company=None):
        return self.env["l10n_cl.dte.rcv.period"].create(
            {
                "company_id": (company or self.company).id,
                "year": year,
                "month": month,
                "operation": operation,
            }
        )

    def _sync(self, period, source="sii"):
        return self.env["l10n_cl.dte.rcv.sync"].create(
            {
                "period_id": period.id,
                "company_id": period.company_id.id,
                "operation": period.operation,
                "source": source,
            }
        )

    @staticmethod
    def _csv(*lines, header=CSV_HEADER):
        return "\n".join((header, *lines)).encode("utf-8")

    def _wizard(self, content, operation="purchase", month=8, user=None):
        env = self.env(user=user or self.manager)
        return env["l10n_cl.dte.rcv.csv.import.wizard"].create(
            {
                "company_id": self.company.id,
                "year": 2026,
                "month": month,
                "operation": operation,
                "upload": base64.b64encode(content),
                "filename": "rcv.csv",
            }
        )
