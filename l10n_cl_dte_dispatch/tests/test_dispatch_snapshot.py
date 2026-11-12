# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import DispatchTestCommon


@tagged("post_install", "-at_install")
class TestDispatchSnapshot(DispatchTestCommon):
    def _reviewed_dispatch(self):
        dispatch = self._create_dispatch()
        self._create_line(dispatch)
        dispatch.action_review()
        return dispatch

    def test_capture_is_deterministic(self):
        # Arrange
        dispatch = self._reviewed_dispatch()
        snapshot = self.env["l10n_cl.dte.dispatch.snapshot"]
        # Act
        first = snapshot.serialize(snapshot.capture(dispatch))
        second = snapshot.serialize(snapshot.capture(dispatch))
        # Assert
        self.assertEqual(first, second)
        self.assertEqual(snapshot.digest(first), snapshot.digest(second))

    def test_digest_changes_when_a_line_changes(self):
        # Arrange
        dispatch = self._reviewed_dispatch()
        snapshot = self.env["l10n_cl.dte.dispatch.snapshot"]
        before = snapshot.digest(snapshot.serialize(snapshot.capture(dispatch)))
        # Act
        dispatch.line_ids.description = "Otro producto"
        after = snapshot.digest(snapshot.serialize(snapshot.capture(dispatch)))
        # Assert
        self.assertNotEqual(before, after)

    def test_freeze_requires_review(self):
        # Arrange
        dispatch = self._create_dispatch()
        self._create_line(dispatch)
        # Act / Assert
        with self.assertRaises(UserError):
            dispatch.action_freeze_snapshot()

    def test_freeze_stores_schema_json_and_digest(self):
        # Arrange
        dispatch = self._reviewed_dispatch()
        # Act
        dispatch.action_freeze_snapshot()
        # Assert
        self.assertEqual(dispatch.state, "frozen")
        self.assertEqual(dispatch.snapshot_schema, "dte52-gd-snapshot-v1")
        self.assertEqual(len(dispatch.snapshot_digest), 64)
        self.assertEqual(json.loads(dispatch.snapshot_json)["name"], dispatch.name)

    def test_frozen_dispatch_rejects_fiscal_changes(self):
        # Arrange
        dispatch = self._reviewed_dispatch()
        dispatch.action_freeze_snapshot()
        # Act / Assert
        with self.assertRaises(UserError):
            dispatch.origin_address = "Otro origen"

    def test_frozen_dispatch_rejects_line_changes(self):
        # Arrange
        dispatch = self._reviewed_dispatch()
        line = dispatch.line_ids
        dispatch.action_freeze_snapshot()
        # Act / Assert
        with self.assertRaises(UserError):
            line.quantity = 5.0
        with self.assertRaises(UserError):
            self._create_line(dispatch)
        with self.assertRaises(UserError):
            line.unlink()
        with self.assertRaises(UserError):
            dispatch.unlink()
