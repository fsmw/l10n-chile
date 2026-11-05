from lxml import etree

from odoo.addons.l10n_cl_dte_note.services.note_snapshot import NoteSnapshotService
from odoo.addons.l10n_cl_dte_note.services.note_xml_composer import NoteXmlComposer
from odoo.addons.l10n_cl_dte_note.tests.common import DteNoteTestCase
from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import (
    SII_NAMESPACE,
    DteXmlComposer,
    _dte_schema,
)


class TestNoteXmlAndWizards(DteNoteTestCase):
    def _credit_snapshot(self):
        source, target, _source_snapshot = self.create_accepted_source()
        caf = self.validate_authorization(
            document_code="61", folio_start=760, folio_end=765
        )
        note = self.create_credit_note(source, target)
        note.action_post()
        snapshot = NoteSnapshotService().capture_note(note, caf, 760, "61")
        snapshot["ted_xml"] = NoteXmlComposer().compose_ted(snapshot, caf)
        return source, target, note, caf, snapshot

    def test_type_61_xml_has_reference_reason(self):
        _source, _target, _note, _caf, snapshot = self._credit_snapshot()
        xml_bytes = NoteXmlComposer().compose_unsigned_document(snapshot)
        self.assertIn(b"<TipoDTE>61</TipoDTE>", xml_bytes)
        self.assertIn(b"<RazonRef>Correcci", xml_bytes)
        self.assertIn(b"<TpoDocRef>33</TpoDocRef>", xml_bytes)

    def test_type_56_xml_has_reference_reason(self):
        _source, _target, _note, _caf, snapshot = self._credit_snapshot()
        snapshot["document_code"] = "56"
        snapshot["folio"] = 761
        snapshot["reference"]["reference_code"] = "3"
        snapshot["reference"]["reason"] = "Aumento de monto"
        xml_bytes = NoteXmlComposer().compose_unsigned_document(snapshot)
        self.assertIn(b"<TipoDTE>56</TipoDTE>", xml_bytes)
        self.assertIn(b"<RazonRef>Aumento de monto</RazonRef>", xml_bytes)

    def test_certification_text_correction_uses_two_references_and_zero_detail(self):
        _source, _target, _note, _caf, snapshot = self._credit_snapshot()
        snapshot["lines"] = [
            {
                "name": "Corrección de texto",
                "description": "Dice: actividad A debe decir: actividad B",
                "quantity": None,
                "unit_price": None,
                "discount_percent": None,
                "subtotal": "0",
                "exempt": False,
                "text_correction": True,
            }
        ]
        snapshot["totals"] = {
            "net": None,
            "exempt": None,
            "vat_rate": None,
            "vat": None,
            "total": "0",
        }
        snapshot["references"] = [
            {
                "line_number": 1,
                "document_type": "SET",
                "folio": "1",
                "date": snapshot["issue_date"],
                "reason": "CASO 12345-1",
                "reference_code": None,
            },
            {
                "line_number": 2,
                "document_type": "33",
                "folio": "120",
                "date": snapshot["issue_date"],
                "reason": "Dice: actividad A debe decir: actividad B",
                "reference_code": "2",
            },
        ]
        unsigned_xml = NoteXmlComposer().compose_unsigned_document(snapshot)
        self.create_signing_profile(environment="demo")
        xml_bytes = SignatureService().sign_document(
            self.company.with_user(self.dte_manager), "demo", unsigned_xml
        )
        root = etree.fromstring(xml_bytes)
        _dte_schema().assertValid(root)
        document = root.find(f"{{{SII_NAMESPACE}}}Documento")
        self.assertEqual(len(document.findall(f"{{{SII_NAMESPACE}}}Referencia")), 2)
        detail = document.find(f"{{{SII_NAMESPACE}}}Detalle")
        self.assertEqual(detail.findtext(f"{{{SII_NAMESPACE}}}MontoItem"), "0")
        self.assertIsNone(detail.find(f"{{{SII_NAMESPACE}}}QtyItem"))
        self.assertIsNone(detail.find(f"{{{SII_NAMESPACE}}}PrcItem"))

    def test_certification_note_signature_survives_envelope_embedding(self):
        _source, _target, _note, caf, snapshot = self._credit_snapshot()
        snapshot["certification"] = {"set_id": 1}
        self.create_signing_profile(environment="demo")
        unsigned_xml = NoteXmlComposer().compose_unsigned_document(snapshot)
        signed_xml = SignatureService().sign_document(
            self.company.with_user(self.dte_manager), "demo", unsigned_xml
        )
        envelope = etree.Element(
            f"{{{SII_NAMESPACE}}}EnvioDTE",
            nsmap={
                None: SII_NAMESPACE,
                "xsi": "http://www.w3.org/2001/XMLSchema-instance",
            },
        )
        set_dte = etree.SubElement(envelope, f"{{{SII_NAMESPACE}}}SetDTE")
        set_dte.append(etree.fromstring(signed_xml))
        embedded_dte = etree.tostring(
            set_dte[0], encoding="ISO-8859-1", xml_declaration=True
        )

        self.assertTrue(DteXmlComposer().validate_document(embedded_dte))

    def test_credit_wizard_creates_note(self):
        source, target, _snapshot = self.create_accepted_source()
        wizard = (
            self.env["account.move.reversal"]
            .with_user(self.dte_manager)
            .create(
                {
                    "move_ids": [(6, 0, [source.id])],
                    "date": source.invoice_date,
                    "journal_id": source.journal_id.id,
                    "reason": "Anulación documentada",
                    "dte_note_operation": "cancel",
                }
            )
        )
        wizard.reverse_moves()
        credit = wizard.new_move_ids.ensure_one()
        self.assertEqual(credit.l10n_latam_document_type_id.code, "61")
        self.assertEqual(credit.dte_note_target_document_id, target)
        self.assertEqual(credit.dte_note_reason, "Anulación documentada")
        self.assertEqual(credit.move_type, "out_refund")

    def test_debit_wizard_creates_type_56(self):
        source, target, _snapshot = self.create_accepted_source()
        wizard = (
            self.env["account.debit.note"]
            .with_user(self.dte_manager)
            .create(
                {
                    "move_ids": [(6, 0, [source.id])],
                    "reason": "Ajuste documentado",
                    "dte_note_operation": "amount",
                }
            )
        )
        values = wizard._prepare_dte_debit_values(source)
        self.assertEqual(
            values["l10n_latam_document_type_id"], self.document_types["56"].id
        )
        self.assertEqual(values["dte_note_target_document_id"], target.id)
        self.assertEqual(values["dte_note_operation"], "amount")

    def test_note_demo_flow_keeps_response_evidence(self):
        source, target, _snapshot = self.create_accepted_source()
        self.validate_authorization(document_code="61", folio_start=770, folio_end=775)
        self.create_signing_profile()
        note = self.create_credit_note(source, target)
        note.action_post()
        issued = note.with_user(self.dte_manager).issue_dte()
        self.assertEqual(issued.document_code, "61")
        self.assertEqual(issued.preparation_state, "queued")
        self.assertTrue(issued.payload_digest)
        submission = self.env["l10n_cl.electronic.submission"].search(
            [("document_id", "=", issued.id)], limit=1
        )
        submission._job_send_to_sii()
        submission.invalidate_recordset()
        self.assertEqual(submission.state, "submitted")
        self.assertTrue(submission.track_id)
        self.assertTrue(
            issued.artifact_ids.filtered(lambda item: item.role == "sii_response")
        )
