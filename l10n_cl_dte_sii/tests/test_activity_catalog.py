# Copyright 2026
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Estado del catálogo de actividades económicas del SII (S8).

El catálogo se genera desde la publicación oficial del SII: 674 códigos de
seis dígitos. Estos tests fallan si una actualización del catálogo pierde
códigos, duplica códigos o degrada el formato que exige el `<Acteco>` del DTE.
"""

import importlib.util
from pathlib import Path

from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestActivityCatalog(TransactionCase):
    def test_catalog_covers_the_official_sii_publication(self):
        activities = self.env["l10n_cl.dte.activity"].search([])
        self.assertEqual(len(activities), 674)
        self.assertEqual(
            activities.filtered(lambda activity: len(activity.code) != 6),
            activities.browse(),
            "Todo Acteco debe tener seis dígitos, incluido el cero inicial.",
        )
        self.assertEqual(len(activities.mapped("code")), len(activities))

    def test_catalog_keeps_leading_zero_codes(self):
        # El CSV del catálogo anterior perdía el cero inicial (11101 en vez de
        # 011101) y ese código no es válido en el DTE.
        activity = self.env["l10n_cl.dte.activity"].search(
            [("code", "=", "011101")], limit=1
        )
        self.assertTrue(activity)
        self.assertEqual(activity.name, "CULTIVO DE TRIGO")

    def test_catalog_display_name_shows_code_and_glosa(self):
        activity = self.env["l10n_cl.dte.activity"].search(
            [("code", "=", "522990")], limit=1
        )
        self.assertTrue(activity)
        self.assertEqual(
            activity.display_name,
            "(522990) OTRAS ACTIVIDADES DE APOYO AL TRANSPORTE N.C.P.",
        )

    def test_migration_links_the_legacy_activity_code(self):
        """La migración 18.0.7.0.0 completa el cero inicial del Char heredado."""
        script = (
            Path(get_module_path("l10n_cl_dte_sii"))
            / "migrations/18.0.7.0.0/post-migrate.py"
        )
        spec = importlib.util.spec_from_file_location("dte_activity_migration", script)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        company = self.env.company
        company.l10n_cl_dte_activity_code = "11101"
        company.l10n_cl_dte_activity_ids = [(5, 0, 0)]
        migration.migrate(self.env.cr, "18.0.6.0.0")
        self.assertEqual(company.l10n_cl_dte_activity_ids.mapped("code"), ["011101"])
