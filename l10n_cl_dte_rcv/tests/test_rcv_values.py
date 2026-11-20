# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import date

from odoo.tests import TransactionCase, tagged

from ..services.rcv_values import (
    is_valid_rut,
    normalize_vat,
    parse_date,
    payload_hash,
    rut_check_digit,
    to_integer,
)


@tagged("post_install", "-at_install")
class TestRcvValues(TransactionCase):
    def test_vat_variants_normalize(self):
        self.assertEqual(normalize_vat("76.222.222-1"), "76222222-1")
        self.assertEqual(normalize_vat(" CL 76.222.222-1 "), "76222222-1")
        self.assertEqual(normalize_vat("CL762222221"), "76222222-1")
        self.assertEqual(normalize_vat(76222222, "1"), "76222222-1")
        self.assertEqual(normalize_vat("1111111k"), "1111111-K")
        self.assertEqual(normalize_vat(None), "")

    def test_rut_check_digit(self):
        self.assertEqual(rut_check_digit("76222222"), "1")
        self.assertEqual(rut_check_digit("12345678"), "5")
        self.assertTrue(is_valid_rut("76222222-1"))
        self.assertFalse(is_valid_rut("76222222-2"))
        self.assertFalse(is_valid_rut("ABC-1"))
        self.assertFalse(is_valid_rut(""))

    def test_dates_in_sii_formats(self):
        expected = date(2026, 8, 23)
        for value in ("2026-08-23", "23/08/2026", "20260823", "23-08-2026"):
            self.assertEqual(parse_date(value), expected)
        self.assertEqual(parse_date(expected), expected)
        self.assertFalse(parse_date(""))
        self.assertFalse(parse_date("31/31/2026"))

    def test_amounts_are_whole_pesos(self):
        self.assertEqual(to_integer(None), 0)
        self.assertEqual(to_integer(""), 0)
        self.assertEqual(to_integer(19), 19)
        self.assertEqual(to_integer(" 1190 "), 1190)
        self.assertEqual(to_integer("-50"), -50)
        self.assertEqual(to_integer("100,0"), 100)
        for value in ("12.5", "abc", "Infinity"):
            with self.assertRaises(ValueError):
                to_integer(value)

    def test_payload_hash_ignores_key_order(self):
        self.assertEqual(payload_hash({"a": 1, "b": 2}), payload_hash({"b": 2, "a": 1}))
        self.assertNotEqual(payload_hash({"a": 1}), payload_hash({"a": 2}))
