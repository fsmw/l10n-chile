# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Synthetic supplier DTEs received by a second company that answers them."""

import base64
import hashlib
from collections import Counter

from lxml import etree

from odoo import Command

from odoo.addons.l10n_cl_dte_inbox.services.incoming_parser import SII_NS
from odoo.addons.l10n_cl_dte_inbox.services.incoming_validation import xml_bytes
from odoo.addons.l10n_cl_dte_signing.services.signature_service import (
    SignatureService,
)
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import DteXmlComposer
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase

from ..services.exchange_signer import (
    validate_exchange_schema,
    verify_exchange_signatures,
)

RECIPIENT_VAT = "76234567-6"
SUPPLIER_VAT = "76864280-K"
SUPPLIER_EMAIL = "dte@supplier.example.com"


class ExchangeCase(DteInvoiceTestCase):
    """``company`` is the supplier; ``recipient`` receives and answers."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.receiver.sudo().vat = RECIPIENT_VAT
        cls.recipient = (
            cls.env["res.company"]
            .sudo()
            .create(
                {
                    "name": "Synthetic exchange recipient",
                    "partner_id": cls.receiver.id,
                    "currency_id": cls.env.ref("base.CLP").id,
                    "country_id": cls.chile.id,
                    "dte_sii_environment": "certification",
                }
            )
        )
        for user in (cls.dte_manager, cls.dte_user):
            user.sudo().write({"company_ids": [Command.link(cls.recipient.id)]})
        cls.supplier_partner = (
            cls.env["res.partner"]
            .sudo()
            .create(
                {
                    "name": "Synthetic supplier",
                    "vat": SUPPLIER_VAT,
                    "email": SUPPLIER_EMAIL,
                    "is_company": True,
                }
            )
        )

    def setUp(self):
        super().setUp()
        self.supplier_profile = self.create_signing_profile()
        self.recipient_profile = self.create_signing_profile(
            company=self.recipient, environment="certification"
        )
        self.answering_env = self.env(
            context=dict(self.env.context, allowed_company_ids=[self.recipient.id])
        )
        self.imports = self.answering_env["l10n_cl.incoming.dte.import"].with_company(
            self.recipient
        )
        self.exchanges = self.answering_env[
            "l10n_cl.incoming.dte.exchange"
        ].with_company(self.recipient)

    # Supplier side ----------------------------------------------------------

    def signed_dte(self, folio=810, partner=None):
        snapshot, caf = self.capture(self.make_invoice(partner=partner), folio=folio)
        composer = DteXmlComposer()
        snapshot["ted_xml"] = composer.compose_ted(snapshot, caf)
        return SignatureService().sign_document(
            self.company.with_user(self.dte_manager),
            "demo",
            composer.compose_unsigned_document(snapshot),
        )

    def envelope(self, *documents):
        root = etree.Element(
            f"{{{SII_NS}}}EnvioDTE", nsmap={None: SII_NS}, version="1.0"
        )
        target = etree.SubElement(root, f"{{{SII_NS}}}SetDTE", ID="exchange-test-set")
        cover = etree.SubElement(target, f"{{{SII_NS}}}Caratula", version="1.0")
        for name, value in (
            ("RutEmisor", SUPPLIER_VAT),
            ("RutEnvia", SUPPLIER_VAT),
            ("RutReceptor", RECIPIENT_VAT),
            ("FchResol", "2020-01-01"),
            ("NroResol", "80"),
            ("TmstFirmaEnv", "2026-10-15T12:00:00"),
        ):
            etree.SubElement(cover, f"{{{SII_NS}}}{name}").text = value
        codes = Counter(
            etree.fromstring(doc).findtext(
                f".//{{{SII_NS}}}Encabezado/{{{SII_NS}}}IdDoc/{{{SII_NS}}}TipoDTE"
            )
            for doc in documents
        )
        for code, count in codes.items():
            subtotal = etree.SubElement(cover, f"{{{SII_NS}}}SubTotDTE")
            etree.SubElement(subtotal, f"{{{SII_NS}}}TpoDTE").text = code
            etree.SubElement(subtotal, f"{{{SII_NS}}}NroDTE").text = str(count)
        for doc in documents:
            target.append(etree.fromstring(doc))
        return SignatureService().sign_envelope(
            self.company.with_user(self.dte_manager), "demo", xml_bytes(root)
        )

    # Recipient side ---------------------------------------------------------

    def receive(self, *folios):
        """Import an envelope with the given folios and review its DTEs."""
        batch = self.imports._import_xml(
            self.envelope(*(self.signed_dte(folio) for folio in folios)),
            filename="EnvioDTE_synthetic.xml",
        )
        self.assertEqual(batch.state, "imported", batch.diagnostics)
        documents = batch.line_ids.incoming_document_id
        documents.action_mark_reviewed()
        return batch, documents

    @staticmethod
    def xml_of(exchange):
        return base64.b64decode(exchange.with_context(bin_size=False).signed_xml)

    def assert_valid_evidence(self, exchange, signatures):
        content = self.xml_of(exchange)
        self.assertEqual(exchange.xml_sha256, hashlib.sha256(content).hexdigest())
        validate_exchange_schema(content)
        self.assertEqual(verify_exchange_signatures(content), signatures)
        return etree.fromstring(content)

    def parse_as_receiver_response(self, content):
        """Parse with the issuer-side parser when that addon is installed."""
        installed = (
            self.env["ir.module.module"]
            .sudo()
            .search_count(
                [
                    ("name", "=", "l10n_cl_dte_issued_events"),
                    ("state", "=", "installed"),
                ]
            )
        )
        if not installed:
            return None
        from odoo.addons.l10n_cl_dte_issued_events.services import (
            receiver_response_parser,
        )

        return receiver_response_parser.parse_receiver_response(
            content, SignatureService()
        )

    @staticmethod
    def text(root, path):
        return root.xpath(path + "/text()", namespaces={"s": SII_NS})
