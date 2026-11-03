from odoo.exceptions import UserError

from .common import CafTestCase


class TestCafFolio(CafTestCase):
    def test_reservation_same_document_idempotent(self):
        authorization = self.validate_authorization()
        document = self.create_document(source_key="folio-idempotent-source")

        first = authorization.action_reserve_folio(document, "same-reservation")
        again = authorization.action_reserve_folio(document, "same-reservation")

        self.assertEqual(first, again)
        self.assertEqual(first.number, 100)
        self.assertEqual(document.folio, 100)
        self.assertEqual(document.preparation_state, "numbered")

    def test_distinct_reservations_get_unique_folios(self):
        authorization = self.validate_authorization()
        documents = [
            self.create_document(source_key=f"folio-distinct-{index}")
            for index in range(2)
        ]

        folios = [
            authorization.action_reserve_folio(document, f"distinct-{index}").number
            for index, document in enumerate(documents)
        ]

        self.assertEqual(folios, [100, 101])

    def test_outside_range_folio_rejected(self):
        authorization = self.validate_authorization(folio_start=130, folio_end=130)
        document = self.create_document(source_key="folio-range-exhausted")
        authorization.action_reserve_folio(document, "only-folio")
        another = self.create_document(source_key="folio-range-overflow")

        with self.assertRaises(UserError):
            authorization.action_reserve_folio(another, "no-folio-left")
        self.assertEqual(another.preparation_state, "draft")

    def test_consumed_or_void_folio_not_reused(self):
        authorization = self.validate_authorization(folio_start=140, folio_end=143)
        voided_document = self.create_document(source_key="folio-voided")
        voided = authorization.action_reserve_folio(voided_document, "void-key")
        voided.action_void("Synthetic test cancellation")
        signed_document = self.create_document(source_key="folio-consumed")
        consumed = authorization.action_reserve_folio(signed_document, "consume-key")
        self.put_document_in_queued_state(signed_document)
        consumed.action_consume()
        next_document = self.create_document(source_key="folio-after-terminal")

        next_folio = authorization.action_reserve_folio(next_document, "next-key")

        self.assertEqual(voided.number, 140)
        self.assertEqual(consumed.number, 141)
        self.assertEqual(next_folio.number, 142)
        self.assertEqual(consumed.state, "issued")
        self.assertEqual(voided.state, "void")
