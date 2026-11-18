# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Shared helpers for golden-XML tests of issued DTEs.

A golden test issues a document end to end and compares its signed XML with a
reviewed file in the calling addon's ``tests/golden/`` folder. To read what a
test covers, open its golden file.

Test keys are generated on every run, so cryptographic values (CAF, TED FRMT,
digests, signature and certificate) are written as ``___ignore___``. Their
correctness is checked separately by ``validate_document`` (XSD, TED and
XMLDSig verification).

To regenerate after an intended XML change, run the tests with
``DTE_GOLDEN_UPDATE=1`` and review the diff of the golden files before
committing.
"""

import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from lxml import etree

from odoo.addons.l10n_cl_dte_xml.services.xml_composer import DteXmlComposer

IGNORE = "___ignore___"
UPDATE_ENV_VAR = "DTE_GOLDEN_UPDATE"
FIXED_TIMESTAMP = "2026-01-15T12:00:00"
CHILE_TIMEZONE = ZoneInfo("America/Santiago")
# Elements whose content depends on keys generated for each test run.
VOLATILE_XPATHS = (
    "//*[local-name()='TED']/*[local-name()='DD']/*[local-name()='CAF']",
    "//*[local-name()='TED']/*[local-name()='FRMT']",
    "//*[local-name()='Signature']//*[local-name()='DigestValue']",
    "//*[local-name()='Signature']/*[local-name()='SignatureValue']",
    "//*[local-name()='Signature']/*[local-name()='KeyInfo']",
)


def masked_xml(signed_xml):
    """Return the signed XML in normalized form, with volatile crypto nodes masked."""
    root = etree.fromstring(signed_xml)
    for xpath in VOLATILE_XPATHS:
        for node in root.xpath(xpath):
            for child in list(node):
                node.remove(child)
            node.attrib.clear()
            node.text = IGNORE
    return _normalized(root)


def normalized_golden(text):
    """Return a golden file's XML in the same normalized form as ``masked_xml``.

    Golden files are reformatted by prettier, so layout and whitespace inside
    text nodes are not significant.
    """
    parser = etree.XMLParser(remove_blank_text=True)
    return _normalized(etree.fromstring(text.encode("utf-8"), parser))


def _normalized(root):
    for node in root.iter():
        node.tail = None
        if node.text is not None:
            node.text = " ".join(node.text.split()) or None
    etree.indent(root, space="  ")
    return etree.tostring(root, encoding="unicode") + "\n"


def assert_golden_xml(testcase, signed_xml, golden_path):
    """Validate the signed DTE strictly, then compare it with its golden file."""
    DteXmlComposer().validate_document(signed_xml, strict_sii=True)
    actual = masked_xml(signed_xml)
    path = Path(golden_path)
    if os.environ.get(UPDATE_ENV_VAR):
        path.parent.mkdir(exist_ok=True)
        path.write_text(actual, encoding="utf-8")
    testcase.assertTrue(path.exists(), f"Missing golden file {path}")
    testcase.assertEqual(actual, normalized_golden(path.read_text(encoding="utf-8")))


@contextmanager
def frozen_chile_now(module_path, timestamp=FIXED_TIMESTAMP):
    """Freeze ``datetime.now()`` inside one service module.

    ``module_path`` is the dotted path of a module that does
    ``from datetime import datetime`` and stamps documents with
    ``datetime.now(...)``.
    """
    frozen = datetime.fromisoformat(timestamp).replace(tzinfo=CHILE_TIMEZONE)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen.astimezone(tz) if tz else frozen.replace(tzinfo=None)

    with patch(f"{module_path}.datetime", FrozenDatetime):
        yield
