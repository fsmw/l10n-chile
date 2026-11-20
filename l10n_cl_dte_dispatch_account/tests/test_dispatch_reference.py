# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_dispatch.tests.common import DispatchRecordMixin
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase


@tagged("post_install", "-at_install")
class TestDispatchReference(DispatchRecordMixin, DteInvoiceTestCase):
    """An invoice reference takes type, folio and date from an issued guide."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dispatch_values = cls.build_dispatch_values(cls.receiver)

    def _guide(self, partner=None, key="manual-0001"):
        self.create_signing_profile()
        self.validate_authorization(document_code="52", folio_start=1, folio_end=10)
        dispatch = self._create_dispatch(
            partner_id=(partner or self.receiver).id, source_key=key
        )
        self._create_line(dispatch)
        dispatch.action_review()
        dispatch.action_freeze_snapshot()
        return dispatch.action_prepare_document()

    def test_reference_copies_the_guide_identity(self):
        guide = self._guide()
        invoice = self.make_invoice("33", post=False)
        reference = self.env["l10n_cl.dte.invoice.reference"].create(
            {
                "move_id": invoice.id,
                "dispatch_document_id": guide.id,
                "document_type_id": self.document_types["33"].id,
                "folio": "999",
                "date": "2020-01-01",
                "reason": "Goods delivered",
            }
        )
        self.assertEqual(reference.document_type_id.code, "52")
        self.assertEqual(reference.folio, str(guide.folio))
        self.assertEqual(str(reference.date), "2026-11-12")
        self.assertEqual(reference.reason, "Goods delivered")
        snapshot = reference._dte_snapshot()[0]
        self.assertEqual(
            (snapshot["document_type"], snapshot["folio"]), ("52", str(guide.folio))
        )

    def test_guide_of_another_customer_is_refused(self):
        other = self.receiver.copy({"name": "Other customer", "vat": "76086428-5"})
        guide = self._guide(partner=other)
        invoice = self.make_invoice("33", post=False)
        with self.assertRaises(ValidationError):
            self.env["l10n_cl.dte.invoice.reference"].create(
                {
                    "move_id": invoice.id,
                    "dispatch_document_id": guide.id,
                    "reason": "Goods delivered",
                }
            )
