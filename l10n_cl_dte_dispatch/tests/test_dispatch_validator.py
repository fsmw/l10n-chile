# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import DispatchTestCommon


@tagged("post_install", "-at_install")
class TestDispatchValidator(DispatchTestCommon):
    def test_dispatch_without_lines_is_rejected(self):
        # Arrange
        dispatch = self._create_dispatch()
        # Act / Assert
        with self.assertRaises(UserError):
            dispatch.action_review()

    def test_zero_quantity_is_rejected(self):
        # Arrange
        dispatch = self._create_dispatch()
        self._create_line(dispatch, quantity=0.0)
        # Act
        errors = self.env["l10n_cl.dte.dispatch.validator"].validate(dispatch)
        # Assert
        self.assertEqual(len(errors), 1)

    def test_taxable_line_needs_a_price(self):
        # Arrange
        dispatch = self._create_dispatch()
        self._create_line(dispatch, unit_price=0.0)
        # Act
        errors = self.env["l10n_cl.dte.dispatch.validator"].validate(dispatch)
        # Assert
        self.assertEqual(len(errors), 1)

    def test_arrival_before_departure_is_rejected(self):
        # Arrange
        dispatch = self._create_dispatch(
            departure_at="2026-11-12 12:00:00", arrival_at="2026-11-12 10:00:00"
        )
        self._create_line(dispatch)
        # Act
        errors = self.env["l10n_cl.dte.dispatch.validator"].validate(dispatch)
        # Assert
        self.assertEqual(len(errors), 1)

    def test_valid_dispatch_moves_to_reviewed(self):
        # Arrange
        dispatch = self._create_dispatch()
        self._create_line(dispatch)
        # Act
        dispatch.action_review()
        # Assert
        self.assertEqual(dispatch.state, "reviewed")
