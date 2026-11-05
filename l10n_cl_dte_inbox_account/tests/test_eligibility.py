# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib

from lxml import etree

from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_inbox.services.incoming_validation import xml_bytes

from ..services.evidence_service import IncomingAccountEvidenceService
from ..services.policy_service import IncomingAccountPolicyService
from .common import NS, IncomingAccountCase


@tagged("post_install", "-at_install")
class TestIncomingAccountEligibility(IncomingAccountCase):
    def test_source_digest_and_receiver(self):
        source = self.source()
        source.sudo()._write_internal({"xml_sha256": "0" * 64})
        self.assert_code("source_digest_mismatch", self.document, source)
        source.sudo()._write_internal(
            {
                "xml_sha256": hashlib.sha256(
                    base64.b64decode(source.sudo().raw_xml)
                ).hexdigest(),
                "receiver_vat": "76864280-K",
            }
        )
        self.assert_code("identity_mismatch", self.document, source)

    def test_supplier_rut_canonicalization(self):
        source = self.source()
        self.supplier.sudo().with_context(no_vat_validation=True).vat = "76.864.280-k"
        child = (
            self.env["res.partner"]
            .sudo()
            .create(
                dict(
                    name="Supplier contact",
                    parent_id=self.supplier.id,
                    vat="76864280-K",
                )
            )
        )
        partner = IncomingAccountPolicyService(self.op_env).resolve_supplier(
            self.recipient, source.issuer_vat
        )
        self.assertEqual(partner.id, self.supplier.id)
        self.assertEqual(child.commercial_partner_id.id, partner.id)

    def test_missing_or_archived_supplier(self):
        source = self.source()
        self.assert_code(
            "supplier_missing",
            IncomingAccountPolicyService(self.op_env).resolve_supplier,
            self.recipient,
            "76000000-0",
        )
        self.supplier.sudo().active = False
        self.assert_code(
            "supplier_archived",
            IncomingAccountPolicyService(self.op_env).resolve_supplier,
            self.recipient,
            source.issuer_vat,
        )

    def test_unsupported_xml_economics(self):
        content = self.signed()
        root = etree.fromstring(content)
        detail = root.find("s:Documento/s:Detalle", NS)
        detail.remove(detail.find("s:QtyItem", NS))
        self.assert_code(
            "unsupported_feature",
            IncomingAccountEvidenceService(self.op_env).extract,
            xml_bytes(root),
        )
        root = etree.fromstring(content)
        etree.SubElement(
            root.find("s:Documento/s:Encabezado/s:Totales", NS),
            "{{{}}}MontoNF".format(NS["s"]),
        ).text = "0"
        self.assert_code(
            "unsupported_feature",
            IncomingAccountEvidenceService(self.op_env).extract,
            xml_bytes(root),
        )

    def test_untrusted_outer_envelope_cannot_bypass_revalidation(self):
        source = self.source()
        raw = base64.b64decode(source.sudo().raw_xml)
        # Keep a genuinely signed child but remove all validation of its wrapper.
        root = etree.Element(
            "{{{}}}EnvioDTE".format(NS["s"]), nsmap={None: NS["s"]}, version="1.0"
        )
        container = etree.SubElement(
            root, "{{{}}}SetDTE".format(NS["s"]), ID="untrusted-wrapper"
        )
        container.append(etree.fromstring(raw))
        envelope = xml_bytes(root)
        source.sudo()._write_internal(
            {
                "raw_xml": base64.b64encode(envelope),
                "xml_sha256": hashlib.sha256(envelope).hexdigest(),
            }
        )
        self.assert_code("source_validation_failed", self.document, source)
