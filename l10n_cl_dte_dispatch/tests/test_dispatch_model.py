# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from psycopg2 import IntegrityError

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestDispatchModel(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "Cliente GD"})
        cls.dispatch_values = {
            "company_id": cls.env.company.id,
            "sii_environment": "certification",
            "source_kind": "manual",
            "source_key": "manual-0001",
            "issue_date": "2026-11-12",
            "partner_id": cls.partner.id,
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

    def _create_line(self, dispatch):
        return self.env["l10n_cl.dte.dispatch.line"].create(
            {
                "dispatch_id": dispatch.id,
                "description": "Producto GD",
                "quantity": 2.0,
                "uom_id": self.env.ref("uom.product_uom_unit").id,
                "unit_price": 1000.0,
                "tax_category": "taxable",
            }
        )

    def _create_event(self, dispatch):
        return self.env["l10n_cl.dte.dispatch.event"].create(
            {
                "dispatch_id": dispatch.id,
                "event_type": "prepared",
                "payload_digest": "abc123",
            }
        )

    def test_name_is_taken_from_sequence_when_not_given(self):
        # Arrange / Act
        dispatch = self._create_dispatch()
        # Assert
        self.assertTrue(dispatch.name.startswith("GD/"))

    def test_new_dispatch_starts_in_draft(self):
        # Arrange / Act
        dispatch = self._create_dispatch()
        # Assert
        self.assertEqual(dispatch.state, "draft")

    def test_source_key_is_unique_per_company(self):
        # Arrange
        self._create_dispatch()
        # Act / Assert
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                self._create_dispatch()

    def test_line_currency_follows_dispatch(self):
        # Arrange
        dispatch = self._create_dispatch()
        # Act
        line = self._create_line(dispatch)
        # Assert
        self.assertEqual(line.currency_id, dispatch.currency_id)

    def test_deleting_dispatch_removes_its_lines(self):
        # Arrange
        dispatch = self._create_dispatch()
        line = self._create_line(dispatch)
        # Act
        dispatch.unlink()
        # Assert
        self.assertFalse(line.exists())

    def test_dispatch_with_events_cannot_be_deleted(self):
        # Arrange
        dispatch = self._create_dispatch()
        self._create_event(dispatch)
        # Act / Assert
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                dispatch.unlink()

    def test_dte_user_can_create_event_but_not_edit_or_delete(self):
        # Arrange
        user = new_test_user(
            self.env, login="gd_user", groups="l10n_cl_dte_core.group_dte_user"
        )
        event = self._create_event(self._create_dispatch())
        event_model = self.env["l10n_cl.dte.dispatch.event"].with_user(user)
        # Act / Assert
        event_model.check_access("create")
        with self.assertRaises(AccessError):
            event.with_user(user).check_access("write")
        with self.assertRaises(AccessError):
            event.with_user(user).check_access("unlink")
