# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from .common import DispatchTestCommon


@tagged("post_install", "-at_install")
class TestDispatchViews(DispatchTestCommon):
    def test_form_view_renders_with_state_buttons(self):
        # Arrange
        view = self.env.ref("l10n_cl_dte_dispatch.view_dispatch_form")
        # Act
        arch = self.env["l10n_cl.dte.dispatch"].get_view(view.id, "form")["arch"]
        # Assert
        self.assertIn('name="action_review"', arch)
        self.assertIn('name="action_freeze_snapshot"', arch)
