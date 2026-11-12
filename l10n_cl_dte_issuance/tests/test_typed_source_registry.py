# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.service.model import get_public_method
from odoo.tests import TransactionCase, tagged

REGISTRY = "l10n_cl.dte.source.registry"


@tagged("post_install", "-at_install")
class TestTypedSourceRegistry(TransactionCase):
    def setUp(self):
        super().setUp()
        self.source_registry = self.env[REGISTRY]

    def _relations(self, mapping):
        return patch.object(
            type(self.source_registry),
            "_dte_source_relations",
            lambda self_: dict(mapping),
        )

    def test_installed_relations_are_all_valid(self):
        # Addons that are installed register through super(); whatever they
        # register must be consistent, with or without any addon installed.
        relations = self.source_registry._dte_source_relations()
        self.assertIsInstance(relations, dict)
        self.assertEqual(
            self.source_registry._dte_source_relation_fields(),
            frozenset(relations.values()),
        )
        for source_model, field_name in relations.items():
            self.assertEqual(
                self.source_registry._dte_source_relation(source_model), field_name
            )

    def test_registered_model_resolves_to_its_field(self):
        # res.company/company_id stands in for a real source relation.
        with self._relations({"res.company": "company_id"}):
            self.assertEqual(
                self.source_registry._dte_source_relation("res.company"), "company_id"
            )
            self.assertEqual(
                self.source_registry._dte_source_relation_fields(),
                frozenset({"company_id"}),
            )

    def test_unregistered_model_is_rejected(self):
        with self._relations({}), self.assertRaises(ValidationError):
            self.source_registry._dte_source_relation("res.partner")

    def test_field_must_exist_on_electronic_document(self):
        with self._relations({"res.company": "no_such_field"}):
            with self.assertRaises(ValidationError):
                self.source_registry._dte_source_relation("res.company")

    def test_field_must_be_many2one_to_the_source_model(self):
        with self._relations({"res.partner": "company_id"}):
            with self.assertRaises(ValidationError):
                self.source_registry._dte_source_relation("res.partner")
        with self._relations({"res.company": "document_code"}):
            with self.assertRaises(ValidationError):
                self.source_registry._dte_source_relation("res.company")

    def test_two_models_cannot_share_one_field(self):
        with self._relations({"res.company": "company_id", "res.users": "company_id"}):
            with self.assertRaises(ValidationError):
                self.source_registry._dte_source_relation_fields()

    def test_registry_methods_are_not_callable_through_rpc(self):
        for method in (
            "_dte_source_relations",
            "_dte_source_relation",
            "_dte_source_relation_fields",
        ):
            with self.assertRaises(AccessError):
                get_public_method(self.source_registry, method)

    def test_patched_registry_leaves_no_state_behind(self):
        before = self.source_registry._dte_source_relations()
        with self._relations({"res.company": "company_id"}):
            self.assertEqual(
                self.source_registry._dte_source_relation("res.company"),
                "company_id",
            )
        self.assertEqual(self.source_registry._dte_source_relations(), before)
        self.assertNotIn("res.company", before)

    def test_adapter_values_must_hold_only_the_registered_relation(self):
        with self._relations({"res.company": "company_id"}):
            check = self.source_registry._check_adapter_values
            check("res.company", {"company_id": 1}, {"note": "x"})
            for source_values, extra_values in (
                ({"create_uid": 1}, {}),
                ({"company_id": 1, "create_uid": 1}, {}),
                ({}, {}),
                ("company_id", {}),
                ({"company_id": 1}, "x"),
                ({"company_id": 1}, {"folio": 1}),
                ({"company_id": 1}, {"company_id": 2}),
            ):
                with self.assertRaises(UserError):
                    check("res.company", source_values, extra_values)

    def test_adapter_check_protects_every_registered_relation(self):
        with self._relations({"res.company": "company_id", "res.users": "create_uid"}):
            with self.assertRaises(UserError):
                self.source_registry._check_adapter_values(
                    "res.company", {"company_id": 1}, {"create_uid": 1}
                )

    def test_adapter_check_rejects_unregistered_models(self):
        with self._relations({}), self.assertRaises(UserError):
            self.source_registry._check_adapter_values(
                "res.partner", {"company_id": 1}, {}
            )

    def test_adapter_check_is_not_callable_through_rpc(self):
        with self.assertRaises(AccessError):
            get_public_method(self.source_registry, "_check_adapter_values")
