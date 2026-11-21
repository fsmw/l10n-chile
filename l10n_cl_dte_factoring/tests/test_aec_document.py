# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from lxml import etree

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService

from ..services.aec_composer import dte_element_bytes, read_dte_identity
from ..services.aec_signer import verify_aec_signatures
from ..services.aec_validation import (
    check_aec_structure,
    check_embedded_bytes,
    validate_aec_schema,
)
from .common import FactoringTestCase

SII = {"sii": "http://www.sii.cl/SiiDte"}


@tagged("post_install", "-at_install")
class TestAecDocument(FactoringTestCase):
    def test_aec_embeds_the_exact_signed_dte_and_verifies(self):
        assignment = self.signed_assignment()
        aec = self.aec_bytes(assignment)
        dte = dte_element_bytes(self.signed_dte)

        self.assertEqual(assignment.state, "signed")
        self.assertEqual(aec.count(dte), 1)
        start = aec.index(dte)
        SignatureService().verify_signature(
            aec[start : start + len(dte)], sii_standalone=True
        )
        result = verify_aec_signatures(aec)
        self.assertEqual(
            result["references"],
            [f"DTEC33F100S{assignment.id}", f"CES33F100S{assignment.id}"]
            + [f"AEC33F100S{assignment.id}"],
        )
        self.assertTrue(
            aec.startswith(b'<?xml version="1.0" encoding="ISO-8859-1"?>\n<AEC ')
        )
        validate_aec_schema(aec)
        self.assertTrue(check_aec_structure(aec, self.signed_dte))
        self.assertTrue(check_embedded_bytes(aec, self.signed_dte))
        self.assertEqual(assignment.aec_sha256, assignment.aec_evidence_id.sha256)
        self.assertEqual(assignment.signer_vat, "12345678-5")
        self.assertEqual(assignment.signer_name, "DTE synthetic test signer")

    def test_aec_states_the_assignment_terms(self):
        assignment = self.signed_assignment()
        root = etree.fromstring(self.aec_bytes(assignment))
        cession = root.xpath(
            "./sii:DocumentoAEC/sii:Cesiones/sii:Cesion/sii:DocumentoCesion",
            namespaces=SII,
        )[0]
        identity = read_dte_identity(self.signed_dte)

        def value(path):
            return cession.xpath(path + "/text()", namespaces=SII)[0]

        self.assertEqual(value("sii:SeqCesion"), "1")
        self.assertEqual(value("sii:IdDTE/sii:Folio"), "100")
        self.assertEqual(value("sii:IdDTE/sii:MntTotal"), identity["MntTotal"])
        self.assertEqual(value("sii:Cedente/sii:RUT"), "12345678-5")
        self.assertEqual(value("sii:Cedente/sii:RUTAutorizado/sii:RUT"), "12345678-5")
        self.assertIn("Ley N° 19.983", value("sii:Cedente/sii:DeclaracionJurada"))
        self.assertEqual(value("sii:Cesionario/sii:RUT"), "76086428-5")
        self.assertEqual(value("sii:MontoCesion"), str(assignment.amount))
        self.assertEqual(value("sii:TmstCesion"), "2026-11-21T10:00:00")
        self.assertEqual(
            root.xpath(
                "./sii:DocumentoAEC/sii:Caratula/sii:RutCesionario/text()",
                namespaces=SII,
            ),
            ["76086428-5"],
        )

    def test_tampered_aec_fails_signature_verification(self):
        aec = self.aec_bytes(self.signed_assignment())
        tampered = aec.replace(b"<MontoCesion>", b"<MontoCesion>9", 1)

        with self.assertRaises(ValidationError):
            verify_aec_signatures(tampered)

    def test_schema_and_structure_reject_invalid_files(self):
        aec = self.aec_bytes(self.signed_assignment())
        with self.assertRaises(ValidationError):
            validate_aec_schema(
                aec.replace(b"<RutCesionario>", b"<RutCesionarioX>", 1).replace(
                    b"</RutCesionario>", b"</RutCesionarioX>", 1
                )
            )
        with self.assertRaises(ValidationError):
            check_aec_structure(
                aec.replace(b"</RUTReceptor><Folio>100<", b"</RUTReceptor><Folio>101<"),
                self.signed_dte,
            )
        other = self.accepted_invoice(folio=101)[2]
        with self.assertRaises(ValidationError):
            check_embedded_bytes(aec, other)

    def test_non_invoice_dte_types_are_refused(self):
        guide = self.signed_dte.replace(
            b"<TipoDTE>33</TipoDTE>", b"<TipoDTE>52</TipoDTE>", 1
        )
        with self.assertRaises(ValidationError):
            read_dte_identity(guide)

    def test_only_accepted_real_mode_invoices_can_be_assigned(self):
        invoice = self.accepted_invoice(fiscal_state="submitted", folio=101)[0]
        self.assertFalse(invoice.dte_assignment_available)
        with self.assertRaises(UserError):
            self.new_assignment(invoice)
        draft = self.make_invoice(post=False)
        with self.assertRaises(UserError):
            self.new_assignment(draft)
        self.assertTrue(self.invoice.dte_assignment_available)

    def test_one_assignment_holds_the_invoice(self):
        assignment = self.signed_assignment()
        self.invoice.invalidate_recordset()
        self.assertEqual(self.invoice.dte_assignment_state, "in_progress")
        self.assertFalse(self.invoice.dte_assignment_available)
        with self.assertRaises(UserError):
            self.new_assignment()
        assignment.action_cancel()
        self.assertEqual(assignment.state, "cancelled")
        self.assertTrue(self.new_assignment())

    def test_terms_are_immutable_after_signature(self):
        assignment = self.signed_assignment()
        with self.assertRaises(UserError):
            assignment.amount = 1
        with self.assertRaises(UserError):
            assignment.unlink()
        draft = self.accepted_invoice(folio=101)[0]
        record = self.new_assignment(draft)
        record.amount = 10_000_000
        with self.assertRaises(ValidationError):
            record.action_build_aec()
