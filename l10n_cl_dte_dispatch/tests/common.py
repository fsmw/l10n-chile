# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import TransactionCase


class DispatchRecordMixin:
    """Build dispatch records. The test class supplies ``cls.env``."""

    @classmethod
    def build_dispatch_values(cls, partner):
        return {
            "company_id": cls.env.company.id,
            "sii_environment": "certification",
            "source_kind": "manual",
            "source_key": "manual-0001",
            "issue_date": "2026-11-12",
            "partner_id": partner.id,
            "transfer_indicator": "1",
            "dispatch_type_code": "1",
            "origin_address": "Origen 123",
            "destination_address": "Destino 456",
            "currency_id": cls.env.company.currency_id.id,
        }

    def _create_dispatch(self, **overrides):
        return self.env["l10n_cl.dte.dispatch"].create(
            dict(self.dispatch_values, **overrides)
        )

    def _create_line(self, dispatch, **overrides):
        values = {
            "dispatch_id": dispatch.id,
            "description": "Producto GD",
            "quantity": 2.0,
            "uom_id": self.env.ref("uom.product_uom_unit").id,
            "unit_price": 1000.0,
            "tax_category": "taxable",
        }
        return self.env["l10n_cl.dte.dispatch.line"].create(dict(values, **overrides))


class DispatchTestCommon(DispatchRecordMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "Cliente GD"})
        cls.dispatch_values = cls.build_dispatch_values(cls.partner)
