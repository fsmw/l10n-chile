# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Offline preparation and evidence-preserving export of SII test sets."""

import base64
import hashlib
import json
from decimal import Decimal

from odoo import _
from odoo.exceptions import UserError, ValidationError

from odoo.addons.l10n_cl_dte_account.services.dte_issuance_service import (
    DteIssuanceService,
)
from odoo.addons.l10n_cl_dte_core.tools.xml_safety import safe_fromstring
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import DteXmlComposer

from .case_generation_service import CertificationCaseGenerationService
from .set_parser import decimal_value

SII = "http://www.sii.cl/SiiDte"


class CertificationSetExecutionService:
    def prepare_documents(self, run):
        run.ensure_one()
        run._check_execution_access()
        run.env.cr.execute(
            "SELECT id FROM l10n_cl_dte_certification_run WHERE id = %s FOR UPDATE",
            [run.id],
        )
        run.invalidate_recordset()
        run.line_ids.invalidate_recordset()
        if run.state not in {"generated", "partial", "prepared"}:
            raise UserError(_("Generate accounting drafts before preparing DTE."))
        selected = run.line_ids.filtered("selected")
        if not selected or any(
            not line.move_id and line.case_id.document_code not in {"56", "61"}
            for line in selected
        ):
            raise UserError(_("Every selected invoice case needs an accounting draft."))
        if any(not line.document_id for line in selected):
            CertificationCaseGenerationService().validate_frozen_configuration(run)
        failures = []
        for line in selected.sorted("sequence"):
            if line.document_id:
                self.validate_case_result(line)
                continue
            try:
                with run.env.cr.savepoint():
                    code = line.case_id.document_code
                    if not line.move_id:
                        if code not in {"56", "61"}:
                            raise UserError(
                                _("Generate the accounting draft before preparing DTE.")
                            )
                        values = CertificationCaseGenerationService().build_move_values(
                            line
                        )
                        move = run.env["account.move"]._create_certification_invoice(
                            values
                        )
                        line._write_internal(
                            {
                                "move_id": move.id,
                                "state": "generated",
                                "diagnostic_code": False,
                                "diagnostic_message": False,
                            }
                        )
                        line.invalidate_recordset(["move_id"])
                        move = line.move_id
                    else:
                        move = line.move_id
                    if code in {"56", "61"}:
                        move = move.with_context(dte_certification_run_line_id=line.id)
                    if code in {"56", "61"}:
                        self._link_note_to_prepared_reference(line)
                        move = line.move_id.with_context(
                            dte_certification_run_line_id=line.id
                        )
                    if move.state == "draft":
                        move.action_post()
                    adapter = move._get_dte_issuance_adapter(code)
                    adapter.validate_source(move, code)
                    caf = run.set_id._caf_with_available_folio(code)
                    caf_id = caf.id
                    if not caf_id:
                        raise UserError(
                            _("Map a validated certification CAF for DTE type %s.")
                            % code
                        )
                    move = move.with_context(dte_caf_authorization_id=caf_id)
                    document = DteIssuanceService().prepare(move, adapter, code)
                    line._write_internal(
                        {
                            "document_id": document.id,
                            "state": "prepared",
                            "diagnostic_code": False,
                            "diagnostic_message": False,
                        }
                    )
                    self.validate_case_result(line)
            except (UserError, ValidationError) as error:
                line._write_internal(
                    {
                        "state": "failed",
                        "diagnostic_code": "preparation_failed",
                        "diagnostic_message": str(error),
                    }
                )
                failures.append(line.case_id.case_identifier)
        run._write_internal(
            {
                "state": "partial" if failures else "prepared",
                "is_partial": bool(
                    failures or run.line_ids.filtered(lambda line: not line.selected)
                ),
                "diagnostics": failures,
            }
        )
        return True

    @staticmethod
    def _link_note_to_prepared_reference(run_line):
        case = run_line.case_id
        move = run_line.move_id.with_context(dte_certification_run_line_id=run_line.id)
        target_line = run_line.run_id.line_ids.filtered(
            lambda candidate: (
                candidate.case_id.case_identifier == case.reference_case_identifier
            )
        )
        if (
            len(target_line) != 1
            or not target_line.document_id
            or target_line.document_id.preparation_state != "signed"
        ):
            raise UserError(
                _("Prepare the earlier referenced SET case before its DTE note.")
            )
        values = {
            "dte_note_target_document_id": target_line.document_id.id,
            "dte_note_operation": case.note_operation,
            "dte_note_reason": case.reference_reason,
        }
        if move.dte_note_target_document_id != target_line.document_id:
            move.write(values)

    def validate_case_result(self, run_line):  # noqa: C901
        run_line.ensure_one()
        document = run_line.document_id
        if not document or document.preparation_state != "signed":
            raise UserError(_("The case does not have a signed DTE."))
        if (
            document.source_move_id != run_line.move_id
            or document.document_code != run_line.case_id.document_code
        ):
            raise UserError(_("The signed DTE has an inconsistent source."))
        artifacts = document.artifact_ids
        snapshots = artifacts.filtered(lambda item: item.role == "source_snapshot")
        signed = document._get_signed_artifact()
        if len(snapshots) != 1 or len(signed) != 1:
            raise UserError(_("The signed DTE has incomplete source or XML evidence."))
        for artifact in snapshots | signed:
            artifact.invalidate_recordset(["content", "sha256", "byte_count"])
            artifact.action_verify_integrity()
        snapshot = json.loads(base64.b64decode(snapshots.content))
        cert = snapshot.get("certification", {})
        if (
            cert.get("set_id") != run_line.run_id.set_id.id
            or cert.get("case_identifier") != run_line.case_id.case_identifier
            or cert.get("revision") != run_line.run_id.revision
            or cert.get("source_digest") != run_line.run_id.set_id.source_digest
            or cert.get("input_digest") != run_line.run_id.input_digest
        ):
            raise UserError(_("The DTE snapshot has the wrong SII case reference."))
        references = snapshot.get("references", [])
        expected_reference_count = (
            2 if run_line.case_id.document_code in {"56", "61"} else 1
        )
        if (
            len(references) != expected_reference_count
            or references[0].get("reason") != f"CASO {run_line.case_id.case_identifier}"
            or references[0].get("document_type") != "SET"
            or references[0].get("folio") != "1"
            or references[0].get("date") != run_line.run_id.issue_date.isoformat()
        ):
            raise UserError(_("The SET reference differs from the case."))
        if expected_reference_count == 2:
            target_line = run_line.run_id.line_ids.filtered(
                lambda line: (
                    line.case_id.case_identifier
                    == run_line.case_id.reference_case_identifier
                )
            )
            target_document = target_line.document_id
            expected_reason = run_line.case_id.reference_reason
            expected_code = run_line.case_id.reference_code
            note_reference = references[1]
            if (
                not target_document
                or note_reference.get("document_type") != target_document.document_code
                or note_reference.get("folio") != str(target_document.folio)
                or note_reference.get("reason") != expected_reason
                or note_reference.get("reference_code") != expected_code
            ):
                raise UserError(_("The DTE note reference differs from its SET case."))
        if self._is_text_correction_case(run_line.case_id):
            details = snapshot.get("lines", [])
            if (
                len(details) != 1
                or not details[0].get("text_correction")
                or details[0].get("subtotal") != "0"
                or details[0].get("quantity") is not None
                or details[0].get("unit_price") is not None
            ):
                raise UserError(_("The text correction detail is invalid."))
        else:
            expected = CertificationCaseGenerationService()._resolved_details(
                run_line.case_id, run_line=run_line
            )
            actual_details = snapshot.get("lines", [])
            if len(actual_details) != len(expected):
                raise UserError(_("The DTE detail count differs from the SII case."))
            for actual, source in zip(actual_details, expected, strict=True):
                expected_discount = (
                    Decimal(0)
                    if run_line.case_id.global_discount_percent and not source["exempt"]
                    else decimal_value(source["discount_percent"])
                )
                if (
                    actual["name"] != source["name"]
                    or Decimal(actual["quantity"]) != decimal_value(source["quantity"])
                    or Decimal(actual["unit_price"])
                    != decimal_value(source["unit_price"])
                    or Decimal(actual.get("discount_percent") or "0")
                    != expected_discount
                    or actual["exempt"] != source["exempt"]
                ):
                    raise UserError(
                        _("The signed snapshot detail differs from the case.")
                    )
        expected_global_discount = (
            {
                "movement": "D",
                "description": "Descuento global",
                "value_type": "%",
                "value": run_line.case_id.global_discount_percent,
            }
            if run_line.case_id.global_discount_percent
            else None
        )
        if snapshot.get("global_discount") != expected_global_discount:
            raise UserError(_("The global discount differs from the SII case."))
        for key, value in (run_line.case_id.expected_totals or {}).items():
            actual = snapshot["totals"].get(key)
            if actual is None or value is None:
                matches = actual is None and value is None
            else:
                matches = decimal_value(actual) == decimal_value(value)
            if not matches:
                raise UserError(_("The DTE total differs from the SII expectation."))
        xml_bytes = base64.b64decode(signed.content)
        DteXmlComposer().validate_document(xml_bytes)
        root = safe_fromstring(xml_bytes)
        prefix = f"{{{SII}}}"
        doc = root.find(prefix + "Documento")
        discount_nodes = doc.findall(prefix + "DscRcgGlobal")
        if expected_global_discount:
            if (
                len(discount_nodes) != 1
                or discount_nodes[0].findtext(prefix + "TpoMov") != "D"
                or discount_nodes[0].findtext(prefix + "TpoValor") != "%"
                or discount_nodes[0].findtext(prefix + "ValorDR")
                != run_line.case_id.global_discount_percent
            ):
                raise UserError(
                    _("The signed XML global discount differs from the case.")
                )
        elif discount_nodes:
            raise UserError(_("The signed XML has an unexpected global discount."))
        actual_references = doc.findall(prefix + "Referencia")
        expected_reference_count = (
            2 if run_line.case_id.document_code in {"56", "61"} else 1
        )
        if (
            doc.findtext(
                prefix + "Encabezado/" + prefix + "IdDoc/" + prefix + "TipoDTE"
            )
            != run_line.case_id.document_code
            or len(actual_references) != expected_reference_count
            or actual_references[0].findtext(prefix + "TpoDocRef") != "SET"
            or actual_references[0].findtext(prefix + "RazonRef")
            != f"CASO {run_line.case_id.case_identifier}"
        ):
            raise UserError(
                _("The signed XML has the wrong DTE type or SET reference.")
            )
        if expected_reference_count == 2:
            target_line = run_line.run_id.line_ids.filtered(
                lambda line: (
                    line.case_id.case_identifier
                    == run_line.case_id.reference_case_identifier
                )
            )
            target_document = target_line.document_id
            note_reference = actual_references[1]
            expected_code = run_line.case_id.reference_code
            if (
                not target_document
                or note_reference.findtext(prefix + "TpoDocRef")
                != target_document.document_code
                or note_reference.findtext(prefix + "FolioRef")
                != str(target_document.folio)
                or note_reference.findtext(prefix + "CodRef") != expected_code
                or note_reference.findtext(prefix + "RazonRef")
                != run_line.case_id.reference_reason
            ):
                raise UserError(
                    _("The signed XML note reference differs from the case.")
                )
        return xml_bytes

    @staticmethod
    def _is_text_correction_case(case):
        if case.note_operation == "text":
            return True
        if case.note_operation != "cancel":
            return False
        target = case.set_id.case_ids.filtered(
            lambda item: item.case_identifier == case.reference_case_identifier
        )
        return bool(target.note_operation == "text")

    def build_envelope(self, run):
        run.ensure_one()
        run._check_execution_access()
        run.env.cr.execute(
            "SELECT id FROM l10n_cl_dte_certification_run WHERE id = %s FOR UPDATE",
            [run.id],
        )
        run.invalidate_recordset()
        run.line_ids.invalidate_recordset()
        from odoo.addons.l10n_cl_dte_gateway.services.envelope_composer import (
            SiiEnvelopeComposer,
        )

        if run.envelope_file:
            data = base64.b64decode(run.envelope_file)
            if hashlib.sha256(data).hexdigest() != run.envelope_digest:
                raise UserError(_("The exported envelope fingerprint has changed."))
            selected = run.line_ids.filtered("selected").sorted("sequence")
            documents = [self.validate_case_result(line) for line in selected]
            SiiEnvelopeComposer().validate(data, expected_count=len(documents))
            return self._download(run)
        if run.state not in {"prepared", "partial"}:
            raise UserError(_("Prepare signed DTE before exporting an envelope."))
        selected = run.line_ids.filtered("selected").sorted("sequence")
        if not selected or any(not line.document_id for line in selected):
            raise UserError(_("All selected cases must have signed DTE."))
        documents = [self.validate_case_result(line) for line in selected]
        certification_set = run.set_id
        header = {
            "id": f"SET-{certification_set.attention_number}-{run.revision}",
            "sender_vat": certification_set.sender_vat,
            "resolution_number": certification_set.resolution_number,
            "resolution_date": certification_set.resolution_date,
        }
        envelope = SiiEnvelopeComposer().compose(run.company_id, documents, header)
        run._write_internal(
            {
                "envelope_file": base64.b64encode(envelope),
                "envelope_filename": "{}{}.xml".format(
                    header["id"], "-PARTIAL" if run.is_partial else ""
                ),
                "envelope_digest": hashlib.sha256(envelope).hexdigest(),
                "state": "exported",
            }
        )
        return self._download(run)

    def _download(self, run):
        return {
            "type": "ir.actions.act_url",
            "url": (
                "/web/content?model=l10n_cl.dte.certification.run"
                f"&id={run.id}&field=envelope_file&filename_field=envelope_filename"
                "&download=true"
            ),
            "target": "self",
        }
