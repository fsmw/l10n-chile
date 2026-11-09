# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import DteCertificationTestCase

OLD_CONSTRAINT = "l10n_cl_dte_certification_set_company_source_attention_uniq"
ACTIVE_INDEX = "l10n_cl_dte_certification_set_active_source_attention_uniq"
ATTENTION = "12345"
SET_SOURCE = (
    b"SET DE PRUEBAS - NUMERO DE ATENCION: 12345\n"
    b"CASO 12345-1\n"
    b"DOCUMENTO FACTURA ELECTRONICA\n"
    b"ITEM\tCANTIDAD\tPRECIO UNITARIO\n"
    b"ITEM 1\tProducto A\t2\t1000\n"
    b"CASO 12345-2\n"
    b"DOCUMENTO NOTA DE CREDITO ELECTRONICA\n"
    b"REFERENCIA FACTURA ELECTRONICA CORRESPONDIENTE A CASO 12345-1\n"
    b"RAZON REFERENCIA: CORRIGE GIRO DEL RECEPTOR\n"
    b"DICE: Giro antiguo\n"
    b"DEBE DECIR: Giro corregido\n"
)


@tagged("post_install", "-at_install")
class TestCertificationSetReimport(DteCertificationTestCase):
    """Keep archived imports immutable while allowing a new active import."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.set_model = cls.env["l10n_cl.dte.certification.set"]
        cls.wizard_model = cls.env["l10n_cl.dte.certification.import.wizard"]

    def _import_set(self, parser_profile="sii_txt_v2"):
        wizard = (
            self.wizard_model.with_user(self.certification_manager)
            .with_company(self.company)
            .create(
                {
                    "company_id": self.company.id,
                    "step": "select",
                    "source_file": base64.b64encode(SET_SOURCE),
                    "source_filename": "SIISetDePruebas12345.txt",
                    "source_encoding": "utf-8",
                    "parser_profile": parser_profile,
                    "accounting_warning_acknowledged": True,
                }
            )
        )
        wizard.action_import()
        active = self.set_model.search(
            [
                ("company_id", "=", self.company.id),
                ("source_digest", "=", hashlib.sha256(SET_SOURCE).hexdigest()),
                ("attention_number", "=", ATTENTION),
                ("state", "!=", "archived"),
            ]
        )
        self.assertEqual(len(active), 1)
        return active

    def _all_sets(self):
        return self.set_model.search(
            [
                ("company_id", "=", self.company.id),
                ("source_digest", "=", hashlib.sha256(SET_SOURCE).hexdigest()),
                ("attention_number", "=", ATTENTION),
            ]
        )

    def test_reimport_reuses_the_active_set(self):
        first = self._import_set()

        second = self._import_set()

        self.assertEqual(first, second)
        self.assertEqual(first.state, "ready")
        self.assertEqual(len(first.case_ids), 2)
        self.assertEqual(self._all_sets(), first)

    def test_reimport_after_archive_creates_a_new_active_set(self):
        archived = self._import_set()
        archived_payload = archived.normalized_payload
        archived.with_user(self.certification_manager).action_archive()

        current = self._import_set()

        self.assertEqual(archived.state, "archived")
        self.assertNotEqual(archived, current)
        self.assertEqual(current.state, "ready")
        self.assertEqual(current.parser_profile, "sii_txt_v2")
        self.assertEqual(len(current.case_ids), 2)
        self.assertFalse(current.run_ids)
        self.assertFalse(current.case_ids & archived.case_ids)
        self.assertEqual(archived.normalized_payload, archived_payload)
        self.assertEqual(len(self._all_sets()), 2)

    def test_archived_history_allows_repeated_new_imports(self):
        first = self._import_set()
        first.with_user(self.certification_manager).action_archive()
        second = self._import_set()
        second.with_user(self.certification_manager).action_archive()

        current = self._import_set()

        sets = self._all_sets()
        self.assertEqual(len(sets), 3)
        self.assertEqual(len(sets.filtered(lambda item: item.state == "archived")), 2)
        self.assertEqual(current, sets - (first | second))
        self.assertFalse(current.case_ids & (first | second).case_ids)
        self.assertFalse(current.run_ids)

    def test_archived_set_cannot_be_reinterpreted(self):
        archived = self._import_set()
        archived.with_user(self.certification_manager).action_archive()

        with self.assertRaises(UserError) as error:
            archived.with_user(self.certification_manager).action_reinterpret_source()

        self.assertIn("Archived SETs are immutable", str(error.exception))

    def test_partial_index_replaces_the_global_unique_constraint(self):
        self.env.cr.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = %s",
            [self.set_model._table],
        )
        self.assertIn(ACTIVE_INDEX, {row[0] for row in self.env.cr.fetchall()})
        self.env.cr.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass",
            [self.set_model._table],
        )
        self.assertNotIn(OLD_CONSTRAINT, {row[0] for row in self.env.cr.fetchall()})
