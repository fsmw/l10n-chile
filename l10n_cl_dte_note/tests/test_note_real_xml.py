# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Validate SII-accepted credit notes kept outside the repository.

Real DTE files hold third-party fiscal data and must never be committed.
Point ``L10N_CL_DTE_REAL_XML_DIR`` to a local folder of accepted DTE XML to
run these checks; without it the tests are skipped.
"""

import os
import re
from pathlib import Path

from odoo.tests import TransactionCase, tagged

from ..services.note_xml_composer import NoteXmlComposer

REAL_XML_DIR = os.environ.get("L10N_CL_DTE_REAL_XML_DIR")
TIPO_DTE = re.compile(rb"<TipoDTE>\s*(\d+)\s*</TipoDTE>")


def real_documents(document_code):
    if not REAL_XML_DIR or not Path(REAL_XML_DIR).is_dir():
        return []
    documents = []
    for path in sorted(Path(REAL_XML_DIR).rglob("*.xml")):
        content = path.read_bytes()
        match = TIPO_DTE.search(content)
        if match and match.group(1).decode() == document_code:
            documents.append((path.name, content))
    return documents


@tagged("post_install", "-at_install", "l10n_cl_dte_real_xml")
class TestCreditNoteRealXml(TransactionCase):
    def test_accepted_credit_notes_pass_note_validation(self):
        documents = real_documents("61")
        if not documents:
            self.skipTest("Set L10N_CL_DTE_REAL_XML_DIR to accepted DTE 61 XML.")
        for name, content in documents:
            with self.subTest(document=name):
                self.assertTrue(NoteXmlComposer().validate_document(content))
