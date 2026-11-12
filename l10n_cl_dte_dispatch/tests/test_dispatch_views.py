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

    def test_list_and_search_views_render(self):
        # Arrange
        model = self.env["l10n_cl.dte.dispatch"]
        # Act / Assert
        self.assertIn("<list", model.get_view(view_type="list")["arch"])
        self.assertIn("<search", model.get_view(view_type="search")["arch"])

    def test_menu_opens_the_dispatch_action(self):
        # Arrange
        menu = self.env.ref("l10n_cl_dte_dispatch.menu_dispatch_guides")
        # Act / Assert
        self.assertEqual(
            menu.action, self.env.ref("l10n_cl_dte_dispatch.action_dispatch")
        )
