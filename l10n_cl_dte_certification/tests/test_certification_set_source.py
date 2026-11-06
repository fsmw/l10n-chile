# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCertificationSetSource(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Source = cls.env["l10n_cl.dte.certification.set"]

    def _import(self, content=b"synthetic SII SET source\n", filename="set.txt"):
        return self.Source._create_from_import(
            self.company, filename, base64.b64encode(content)
        )

    def test_import_preserves_original_bytes_and_digest(self):
        source = b"synthetic SET\r\n"
        record = self._import(source)
        self.assertEqual(base64.b64decode(record.source_file), source)
        self.assertEqual(record.source_digest, hashlib.sha256(source).hexdigest())
        self.assertEqual(record.source_byte_count, len(source))
        self.assertEqual(record.imported_by, self.env.user)

    def test_import_rejects_non_txt_and_empty_files(self):
        with self.assertRaises(ValidationError):
            self._import(filename="set.xml")
        with self.assertRaises(ValidationError):
            self._import(content=b"")

    def test_import_rejects_duplicate_source_for_company(self):
        self._import()
        with self.assertRaises(ValidationError):
            self._import()
