# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase

from .common import DispatchRecordMixin


@tagged("post_install", "-at_install")
class TestDispatchIssuance(DispatchRecordMixin, DteInvoiceTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dispatch_values = cls.build_dispatch_values(cls.receiver)

    def _frozen_dispatch(self, partner=None):
        dispatch = self._create_dispatch(partner_id=(partner or self.receiver).id)
        self._create_line(dispatch)
        dispatch.action_review()
        dispatch.action_freeze_snapshot()
        return dispatch

    def _prepare_setup(self):
        self.create_signing_profile()
        self.validate_authorization(document_code="52", folio_start=1, folio_end=10)

    def test_prepare_signs_the_guide_and_keeps_its_evidence(self):
        # Arrange
        self._prepare_setup()
        dispatch = self._frozen_dispatch()
        # Act
        document = dispatch.action_prepare_document()
        # Assert
        self.assertEqual(document.preparation_state, "signed")
        self.assertEqual(document.source_dispatch_id, dispatch)
        self.assertGreater(document.folio, 0)
        roles = set(document.artifact_ids.mapped("role"))
        self.assertTrue({"source_snapshot", "unsigned_xml", "signed_xml"} <= roles)

    def test_prepare_requires_a_frozen_dispatch(self):
        # Arrange
        self._prepare_setup()
        dispatch = self._create_dispatch()
        self._create_line(dispatch)
        dispatch.action_review()
        # Act / Assert
        with self.assertRaises(UserError):
            dispatch.action_prepare_document()

    def test_prepare_twice_returns_the_same_document(self):
        # Arrange
        self._prepare_setup()
        dispatch = self._frozen_dispatch()
        first = dispatch.action_prepare_document()
        # Act
        second = dispatch.action_prepare_document()
        # Assert
        self.assertEqual(first, second)

    def test_receiver_without_rut_is_rejected(self):
        # Arrange
        self._prepare_setup()
        partner = self.env["res.partner"].create({"name": "Sin RUT"})
        dispatch = self._frozen_dispatch(partner=partner)
        # Act / Assert
        with self.assertRaises(UserError):
            dispatch.action_prepare_document()
