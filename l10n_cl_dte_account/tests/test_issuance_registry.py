# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.l10n_cl_dte_account.services.dte_issuance_service import (
    DteIssuanceService as ShimService,
)
from odoo.addons.l10n_cl_dte_issuance.services.dte_issuance_service import (
    DteIssuanceService,
)

REGISTRY = "l10n_cl.dte.source.registry"


@tagged("post_install", "-at_install")
class TestIssuanceRegistry(TransactionCase):
    def setUp(self):
        super().setUp()
        self.source_registry = self.env[REGISTRY]

    def _patch_relations(self, mapping):
        return patch.object(
            type(self.source_registry),
            "_dte_source_relations",
            lambda self_: dict(mapping),
        )

    def _adapter(self, source_values):
        adapter = MagicMock()
        adapter.source_key.return_value = "registry-test:1"
        adapter.document_source_values.return_value = source_values
        adapter.prepare_document_values.return_value = {}
        return adapter

    def test_legacy_import_path_still_resolves_to_the_service(self):
        self.assertIs(ShimService, DteIssuanceService)

    def test_account_move_is_registered_as_a_source(self):
        self.assertEqual(
            self.source_registry._dte_source_relation("account.move"),
            "source_move_id",
        )

    def test_protected_fields_follow_the_registry(self):
        mapping = {"account.move": "source_move_id", "res.users": "create_uid"}
        with self._patch_relations(mapping):
            protected = DteIssuanceService()._protected_document_fields(self.env)
        self.assertIn("source_move_id", protected)
        self.assertIn("create_uid", protected)
        self.assertIn("folio", protected)

    def test_unregistered_source_model_is_rejected(self):
        partner = self.env.company.partner_id
        adapter = self._adapter({"source_move_id": partner.id})
        user = self.env.ref("base.user_admin")
        user.groups_id |= self.env.ref("l10n_cl_dte_core.group_dte_user")
        service = DteIssuanceService()
        with self.assertRaises(UserError):
            service.prepare(partner.with_user(user), adapter, "33")

    def test_adapter_relation_must_match_the_registered_one(self):
        move = self.env["account.move"].create({"move_type": "out_invoice"})
        adapter = self._adapter({"source_pos_order_id": 1})
        user = self.env.ref("base.user_admin")
        user.groups_id |= self.env.ref("l10n_cl_dte_core.group_dte_user")
        with self.assertRaises(UserError):
            DteIssuanceService().prepare(move.with_user(user), adapter, "33")
