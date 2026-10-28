# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import hashlib
import json
from collections import defaultdict
from datetime import datetime

from odoo import _, api, fields, models


class RcvNormalizer(models.AbstractModel):
    _name = "l10n.cl.rcv.normalizer"
    _description = "SII RCV response normalizer"

    @api.model
    def _vat(self, rut, dv=None):
        raw = "".join(str(rut or "").split()).replace(".", "").upper()
        if raw.startswith("CL"):
            raw = raw[2:]
        if "-" in raw:
            rut, check_digit = raw.rsplit("-", 1)
            return f"{rut.strip('-')}-{check_digit.upper()}"
        if dv:
            return f"{raw}-{str(dv).strip().upper()}"
        if len(raw) > 1:
            return f"{raw[:-1]}-{raw[-1]}"
        return raw

    @api.model
    def _date(self, value):
        if not value:
            return False
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y%m%d"):
            try:
                return datetime.strptime(value[:10], fmt).date()
            except (ValueError, TypeError):
                continue
        return False

    @api.model
    def _hash(self, row):
        raw = json.dumps(
            row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @api.model
    def _apply(self, sync, data):
        summary_model = self.env["l10n.cl.rcv.summary"]
        document_model = self.env["l10n.cl.rcv.document"]
        observation_model = self.env["l10n.cl.rcv.observation"]
        section_model = self.env["l10n.cl.rcv.section"]
        observed_detail_count = 0
        incomplete = False
        full_payload = json.dumps(
            data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        )
        sync.response_hash = hashlib.sha256(full_payload.encode("utf-8")).hexdigest()
        for section in data:
            state = section.get("state") or "DESCONOCIDO"
            try:
                with self.env.cr.savepoint():
                    rows = (section.get("summary") or {}).get("data") or []
                    expected = {}
                    for row in rows:
                        if not isinstance(row, dict):
                            raise ValueError("Fila de resumen RCV inválida.")
                        type_code = str(
                            row.get("rsmnTipoDocInteger") or row.get("dcvCodigo") or ""
                        )
                        if not type_code:
                            raise ValueError("Resumen RCV sin tipo documental.")
                        expected[type_code] = self._integer(row.get("rsmnTotDoc"))
                        summary_model.create(
                            {
                                "sync_id": sync.id,
                                "sii_state": state,
                                "document_type_code": type_code,
                                "document_type_name": row.get("dcvNombreTipoDoc"),
                                "document_count": expected[type_code],
                                "net_amount": self._integer(row.get("rsmnMntNeto")),
                                "exempt_amount": self._integer(row.get("rsmnMntExe")),
                                "vat_amount": self._integer(row.get("rsmnMntIVA")),
                                "vat_not_recoverable": self._integer(
                                    row.get("rsmnMntIVANoRec")
                                ),
                                "common_vat": self._integer(row.get("rsmnIVAUsoComun")),
                                "total_amount": self._integer(row.get("rsmnMntTotal")),
                            }
                        )
                    details_by_type = section.get("details", {})
                    unavailable = set(section.get("unavailable_details", []))
                    section_incomplete = bool(section.get("fetch_error") or unavailable)
                    section_count = 0
                    for type_code, count in expected.items():
                        details = details_by_type.get(type_code)
                        if (
                            details is None
                            or len(details) != count
                            or type_code in unavailable
                        ):
                            section_incomplete = True
                        summary_row = next(
                            item
                            for item in rows
                            if str(
                                item.get("rsmnTipoDocInteger")
                                or item.get("dcvCodigo")
                                or ""
                            )
                            == type_code
                        )
                        for source_key, detail_key in (
                            ("rsmnMntNeto", "detMntNeto"),
                            ("rsmnMntExe", "detMntExe"),
                            ("rsmnMntIVA", "detMntIVA"),
                            ("rsmnMntTotal", "detMntTotal"),
                        ):
                            expected_amount = self._integer(summary_row.get(source_key))
                            actual_amount = sum(
                                self._integer(item.get(detail_key))
                                for item in (details or [])
                                if isinstance(item, dict)
                            )
                            if details is not None and expected_amount != actual_amount:
                                section_incomplete = True
                        for row in details or []:
                            if not isinstance(row, dict):
                                section_incomplete = True
                                continue
                            document = self._document_values(
                                sync, type_code, row, state
                            )
                            document["tax_breakdown_available"] = row.get(
                                "_tax_breakdown_available",
                                all(
                                    key in row and row[key] not in (None, "")
                                    for key in (
                                        "detMntIVA",
                                        "detMntIVANoRec",
                                        "detIVAUsoComun",
                                    )
                                ),
                            )
                            if not document["issuer_vat"] or not document["folio"]:
                                section_incomplete = True
                                continue
                            identity = [
                                ("company_id", "=", sync.company_id.id),
                                ("operation", "=", sync.operation),
                                ("document_type_code", "=", type_code),
                                ("issuer_vat", "=", document["issuer_vat"]),
                                ("folio", "=", document["folio"]),
                            ]
                            existing = document_model.search(identity, limit=1)
                            document["last_sync_id"] = sync.id
                            if existing:
                                existing.write(document)
                                fiscal_document = existing
                            else:
                                fiscal_document = document_model.create(document)
                            if fiscal_document.reconciliation_state == "missing":
                                fiscal_document.reconciliation_state = "pending"
                            observation_hash = self._hash(row)
                            if not observation_model.search_count(
                                [
                                    ("sync_id", "=", sync.id),
                                    ("payload_hash", "=", observation_hash),
                                ]
                            ):
                                observation_model.create(
                                    {
                                        "document_id": fiscal_document.id,
                                        "sync_id": sync.id,
                                        "payload_hash": observation_hash,
                                        "tax_period": fiscal_document.tax_period,
                                        "sii_state": state,
                                        "net_amount": fiscal_document.net_amount,
                                        "exempt_amount": fiscal_document.exempt_amount,
                                        "vat_amount": fiscal_document.vat_amount,
                                        "vat_not_recoverable": (
                                            fiscal_document.vat_not_recoverable
                                        ),
                                        "common_vat": fiscal_document.common_vat,
                                        "tax_breakdown_available": (
                                            fiscal_document.tax_breakdown_available
                                        ),
                                        "total_amount": fiscal_document.total_amount,
                                    }
                                )
                            section_count += 1
                    section_model.create(
                        {
                            "sync_id": sync.id,
                            "sii_state": state,
                            "state": "failed"
                            if section.get("fetch_error")
                            else "partial"
                            if section_incomplete
                            else "complete",
                            "document_type_codes": ",".join(sorted(expected)),
                            "expected_document_count": sum(expected.values()),
                            "observed_document_count": section_count,
                            "error_message": (
                                _(
                                    "No se pudo consultar la sección RCV. "
                                    "Revise el acceso o cargue el CSV del período."
                                )
                                if section.get("fetch_error")
                                else _(
                                    "Detalle RCV incompleto. "
                                    "Verifique acceso al SII o importe el CSV."
                                )
                            )
                            if section_incomplete
                            else False,
                        }
                    )
                    observed_detail_count += section_count
                    incomplete = incomplete or section_incomplete
            except Exception as exc:
                incomplete = True
                section_model.create(
                    {
                        "sync_id": sync.id,
                        "sii_state": state,
                        "state": "failed",
                        "document_type_codes": ",".join(section.get("type_codes", [])),
                        "error_message": _(
                            "Falló la validación o persistencia de esta sección (%s). "
                            "No se infiere ausencia de documentos."
                        )
                        % type(exc).__name__,
                    }
                )
        sync.write(
            {
                "summary_count": len(sync.summary_ids),
                "detail_count": observed_detail_count,
            }
        )
        self.env["l10n.cl.rcv.matcher"]._reconcile(
            document_model.search([("last_sync_id", "=", sync.id)])
        )
        self.env["l10n.cl.rcv.matcher"]._reconcile_moves(sync)
        return "partial" if incomplete else "success"

    @api.model
    def _apply_csv(self, sync, rows):
        grouped = defaultdict(list)
        for row in rows:
            type_code = str(row.get("tipo_documento") or "").strip()
            if not type_code or not row.get("folio") or not row.get("rut_contraparte"):
                raise ValueError("CSV con documento sin tipo, folio o RUT contraparte.")
            rut_parts = row["rut_contraparte"].replace(".", "").split("-")
            if len(rut_parts) != 2:
                raise ValueError("RUT de contraparte CSV inválido.")
            grouped[type_code].append(
                {
                    "detTipoDoc": int(type_code),
                    "detNroDoc": row["folio"].strip(),
                    "detFchDoc": row.get("fecha"),
                    "detRutDoc": rut_parts[0],
                    "detDvDoc": rut_parts[1],
                    "detRznSoc": row.get("razon_social"),
                    "detMntNeto": int(row.get("neto") or 0),
                    "detMntExe": int(row.get("exento") or 0),
                    "detMntIVA": int(row.get("iva") or 0),
                    "detMntIVANoRec": int(row.get("iva_no_recuperable") or 0),
                    "detIVAUsoComun": int(row.get("iva_uso_comun") or 0),
                    "_tax_breakdown_available": all(
                        row.get(field) not in (None, "")
                        for field in ("iva", "iva_no_recuperable", "iva_uso_comun")
                    ),
                    "detMntTotal": int(row.get("total") or 0),
                }
            )
        # CSV line-level imports have no authoritative SII summary; their rows are
        # the source data and must remain visibly marked as a CSV snapshot.
        summary_rows = []
        details = {}
        for type_code, docs in grouped.items():
            summary_rows.append(
                {
                    "rsmnTipoDocInteger": int(type_code),
                    "dcvNombreTipoDoc": "",
                    "rsmnTotDoc": len(docs),
                    "rsmnMntNeto": sum(item["detMntNeto"] for item in docs),
                    "rsmnMntExe": sum(item["detMntExe"] for item in docs),
                    "rsmnMntIVA": sum(item["detMntIVA"] for item in docs),
                    "rsmnMntIVANoRec": sum(item["detMntIVANoRec"] for item in docs),
                    "rsmnIVAUsoComun": sum(item["detIVAUsoComun"] for item in docs),
                    "rsmnMntTotal": sum(item["detMntTotal"] for item in docs),
                }
            )
            details[type_code] = docs
        result = self._apply(
            sync,
            [{"state": "CSV", "summary": {"data": summary_rows}, "details": details}],
        )
        sync.write(
            {
                "state": result,
                "finished_at": fields.Datetime.now(),
                "summary_count": len(summary_rows),
                "detail_count": len(rows),
            }
        )
        sync.period_id.write(
            {
                "state": result,
                "last_sync_at": fields.Datetime.now(),
                "current_sync_id": sync.id,
            }
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "l10n.cl.rcv.sync",
            "res_id": sync.id,
            "view_mode": "form",
            "target": "current",
        }

    @api.model
    def _document_values(self, sync, type_code, row, state):
        company_vat = self._vat(sync.company_id.vat)
        document_rut = self._vat(row.get("detRutDoc"), row.get("detDvDoc"))
        is_purchase = sync.operation == "purchase"
        issuer_vat = document_rut if is_purchase else company_vat
        counterpart_vat = company_vat if is_purchase else document_rut
        return {
            "company_id": sync.company_id.id,
            "operation": sync.operation,
            "source": sync.source,
            "tax_period": f"{sync.period_id.year:04d}{sync.period_id.month:02d}",
            "document_type_code": str(row.get("detTipoDoc") or type_code),
            "document_type_name": row.get("descTipoTransaccion"),
            "issuer_vat": issuer_vat,
            "issuer_name": row.get("detRznSoc")
            if is_purchase
            else sync.company_id.name,
            "counterpart_vat": counterpart_vat,
            "folio": str(row.get("detNroDoc") or ""),
            "document_date": self._date(row.get("detFchDoc")),
            "sii_state": state,
            "net_amount": row.get("detMntNeto") or 0,
            "exempt_amount": row.get("detMntExe") or 0,
            "vat_amount": row.get("detMntIVA") or 0,
            "vat_not_recoverable": row.get("detMntIVANoRec") or 0,
            "common_vat": row.get("detIVAUsoComun") or 0,
            "total_amount": row.get("detMntTotal") or 0,
        }

    @api.model
    def _integer(self, value):
        if value in (None, ""):
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(float(str(value).replace(",", ".")))
