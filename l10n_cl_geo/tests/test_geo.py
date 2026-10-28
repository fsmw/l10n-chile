# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestGeo(TransactionCase):
    def _chilean_cities(self):
        return self.env["res.city"].search([("country_id.code", "=", "CL")])

    def test_commune_count(self):
        self.assertEqual(len(self._chilean_cities()), 346)

    def test_all_communes_have_a_region(self):
        cities = self._chilean_cities()
        self.assertEqual(len(cities.mapped("state_id")), 16)
        self.assertFalse(cities.filtered(lambda city: not city.state_id))

    def test_nuble_communes_use_nuble(self):
        city = self.env.ref("l10n_cl_geo.city_cl_cl08401")
        self.assertEqual(city.name, "Chillán")
        self.assertEqual(city.state_id.code, "CL-NB")

    def test_no_duplicate_commune_per_region(self):
        cities = self._chilean_cities()
        keys = [(city.name, city.state_id.id) for city in cities]
        self.assertEqual(len(keys), len(set(keys)))
