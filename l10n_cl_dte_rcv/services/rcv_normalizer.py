# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Store an RCV read (SII sections or a CSV file) as Odoo records."""

import logging
from collections import defaultdict

import psycopg2

from odoo import fields
from odoo.exceptions import UserError, ValidationError

from .rcv_values import normalize_vat, parse_date, payload_hash, to_integer

_logger = logging.getLogger(__name__)

CSV_SECTION_STATE = "CSV"
UNKNOWN_SECTION_STATE = "UNKNOWN"
MAX_STORED_ERRORS = 50
# A section that fails for any of these reasons is rolled back on its own and
# recorded as failed; the other sections of the read are kept.
SECTION_ERRORS = (
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    UserError,
    ValidationError,
    psycopg2.Error,
)
SUMMARY_AMOUNT_KEYS = {
    "net_amount": "rsmnMntNeto",
    "exempt_amount": "rsmnMntExe",
    "vat_amount": "rsmnMntIVA",
    "vat_not_recoverable": "rsmnMntIVANoRec",
    "common_vat": "rsmnIVAUsoComun",
    "total_amount": "rsmnMntTotal",
}
DETAIL_AMOUNT_KEYS = {
    "net_amount": "detMntNeto",
    "exempt_amount": "detMntExe",
    "vat_amount": "detMntIVA",
    "vat_not_recoverable": "detMntIVANoRec",
    "common_vat": "detIVAUsoComun",
    "total_amount": "detMntTotal",
}
# Summary totals that must equal the sum of the detail rows.
CHECKED_AMOUNTS = ("net_amount", "exempt_amount", "vat_amount", "total_amount")
BREAKDOWN_KEYS = ("detMntIVA", "detMntIVANoRec", "detIVAUsoComun")
OBSERVED_FIELDS = (
    "tax_period",
    "net_amount",
    "exempt_amount",
    "vat_amount",
    "vat_not_recoverable",
    "common_vat",
    "tax_breakdown_available",
    "total_amount",
)


def _summary_type_code(row):
    return str(row.get("rsmnTipoDocInteger") or row.get("dcvCodigo") or "")


class RcvNormalizer:
    """Persist RCV sections into summaries, documents and observations.

    A *section* is one SII state (``REGISTRO``, ``PENDIENTE``...) as a dict
    with ``state``, ``summary.data`` (summary rows), ``details`` (detail rows
    by document type code) and optionally ``unavailable_details`` and
    ``fetch_error``. Each section is stored in its own savepoint. A section
    whose details do not prove its summary is kept as ``partial``: absence of
    a document is never inferred from an incomplete read.
    """

    def __init__(self, env):
        self.env = env

    def apply(self, sync, sections, notify=True):
        """Store ``sections`` on ``sync`` and return ``success`` or ``partial``.

        Unless ``notify`` is False, ``sync._on_rcv_documents_stored()`` runs
        once everything is stored.
        """
        if not sync.response_hash:
            sync.response_hash = payload_hash(sections)
        observed, incomplete = 0, False
        for section in sections:
            count, section_incomplete = self._apply_section_safely(sync, section)
            observed += count
            incomplete = incomplete or section_incomplete
        sync.write({"summary_count": len(sync.summary_ids), "detail_count": observed})
        if notify:
            sync._on_rcv_documents_stored()
        return "partial" if incomplete else "success"

    def apply_csv(self, sync, result):
        """Store a parsed CSV (``RcvCsvResult``) on ``sync``; return its state."""
        state = self.apply(sync, [self._csv_section(result.rows)], notify=False)
        if result.errors:
            state = "partial" if result.rows else "failed"
            self._record_csv_errors(sync, result.errors, state)
        # Invalid rows make the section partial before anything is concluded.
        sync._on_rcv_documents_stored()
        now = fields.Datetime.now()
        sync.write({"state": state, "finished_at": now})
        sync.period_id.write(
            {"state": state, "last_sync_at": now, "current_sync_id": sync.id}
        )
        return state

    # Sections

    def _apply_section_safely(self, sync, section):
        state = self._section_state(section)
        try:
            with self.env.cr.savepoint():
                return self._apply_section(sync, section, state)
        except SECTION_ERRORS as error:
            _logger.info(
                "RCV sync %s: section %s rolled back (%s).",
                sync.id,
                state,
                type(error).__name__,
            )
            type_codes = section.get("type_codes") if isinstance(section, dict) else []
            self.env["l10n_cl.dte.rcv.section"].create(
                {
                    "sync_id": sync.id,
                    "sii_state": state,
                    "state": "failed",
                    "document_type_codes": ",".join(map(str, type_codes or [])),
                    "error_message": self.env._(
                        "Validating or storing this section failed (%s). "
                        "The absence of documents is not inferred.",
                        type(error).__name__,
                    ),
                }
            )
            return 0, True

    def _section_state(self, section):
        if not isinstance(section, dict):
            return UNKNOWN_SECTION_STATE
        return str(section.get("state") or UNKNOWN_SECTION_STATE)

    def _apply_section(self, sync, section, state):
        expected = self._store_summaries(sync, state, section)
        details_by_type = section.get("details") or {}
        unavailable = set(section.get("unavailable_details") or [])
        incomplete = bool(section.get("fetch_error") or unavailable)
        stored = 0
        for type_code, (expected_count, summary_row) in expected.items():
            details = details_by_type.get(type_code)
            if self._details_mismatch(details, expected_count, summary_row):
                incomplete = True
            incomplete = incomplete or type_code in unavailable
            for row in details or []:
                if self._store_detail(sync, type_code, row, state):
                    stored += 1
                else:
                    incomplete = True
        self._create_section(sync, section, state, expected, stored, incomplete)
        return stored, incomplete

    def _store_summaries(self, sync, state, section):
        rows = (section.get("summary") or {}).get("data") or []
        expected = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("invalid RCV summary row")
            type_code = _summary_type_code(row)
            if not type_code:
                raise ValueError("RCV summary row without document type")
            count = to_integer(row.get("rsmnTotDoc"))
            expected[type_code] = (count, row)
            values = {
                field: to_integer(row.get(key))
                for field, key in SUMMARY_AMOUNT_KEYS.items()
            }
            values.update(
                {
                    "sync_id": sync.id,
                    "sii_state": state,
                    "document_type_code": type_code,
                    "document_type_name": row.get("dcvNombreTipoDoc"),
                    "document_count": count,
                }
            )
            self.env["l10n_cl.dte.rcv.summary"].create(values)
        return expected

    def _details_mismatch(self, details, expected_count, summary_row):
        if details is None or len(details) != expected_count:
            return True
        for field in CHECKED_AMOUNTS:
            detail_key = DETAIL_AMOUNT_KEYS[field]
            actual = sum(
                to_integer(item.get(detail_key))
                for item in details
                if isinstance(item, dict)
            )
            if actual != to_integer(summary_row.get(SUMMARY_AMOUNT_KEYS[field])):
                return True
        return False

    def _create_section(self, sync, section, state, expected, stored, incomplete):
        env = self.env
        if section.get("fetch_error"):
            section_state = "failed"
            message = env._(
                "The RCV section could not be read. "
                "Check the SII access or import the period CSV."
            )
        elif incomplete:
            section_state = "partial"
            message = env._(
                "The RCV detail is incomplete. "
                "Check the SII access or import the period CSV."
            )
        else:
            section_state, message = "complete", False
        return env["l10n_cl.dte.rcv.section"].create(
            {
                "sync_id": sync.id,
                "sii_state": state,
                "state": section_state,
                "document_type_codes": ",".join(sorted(expected)),
                "expected_document_count": sum(item[0] for item in expected.values()),
                "observed_document_count": stored,
                "error_message": message,
            }
        )

    # Documents

    def _store_detail(self, sync, type_code, row, state):
        """Create or update the fiscal document of ``row``; False if unusable."""
        if not isinstance(row, dict):
            return False
        values = self._document_values(sync, type_code, row, state)
        if not values["issuer_vat"] or not values["folio"]:
            return False
        document_model = self.env["l10n_cl.dte.rcv.document"]
        document = document_model.with_context(active_test=False).search(
            [
                ("company_id", "=", sync.company_id.id),
                ("operation", "=", sync.operation),
                ("document_type_code", "=", values["document_type_code"]),
                ("issuer_vat", "=", values["issuer_vat"]),
                ("folio", "=", values["folio"]),
            ],
            limit=1,
        )
        if document:
            document.write(values)
        else:
            document = document_model.create(values)
        self._observe(sync, document, row, state)
        return document

    def _document_values(self, sync, type_code, row, state):
        company_vat = normalize_vat(sync.company_id.vat)
        document_vat = normalize_vat(row.get("detRutDoc"), row.get("detDvDoc"))
        is_purchase = sync.operation == "purchase"
        values = {
            field: to_integer(row.get(key)) for field, key in DETAIL_AMOUNT_KEYS.items()
        }
        values.update(
            {
                "company_id": sync.company_id.id,
                "operation": sync.operation,
                "source": sync.source,
                "tax_period": sync.period_id.tax_period,
                "document_type_code": str(row.get("detTipoDoc") or type_code),
                "document_type_name": row.get("descTipoTransaccion"),
                "issuer_vat": document_vat if is_purchase else company_vat,
                "issuer_name": (
                    row.get("detRznSoc") if is_purchase else sync.company_id.name
                ),
                "counterpart_vat": company_vat if is_purchase else document_vat,
                "counterpart_name": (
                    sync.company_id.name if is_purchase else row.get("detRznSoc")
                ),
                "folio": str(row.get("detNroDoc") or ""),
                "document_date": parse_date(row.get("detFchDoc")),
                "sii_state": state,
                "tax_breakdown_available": row.get(
                    "_tax_breakdown_available",
                    all(row.get(key) not in (None, "") for key in BREAKDOWN_KEYS),
                ),
                "last_sync_id": sync.id,
            }
        )
        return values

    def _observe(self, sync, document, row, state):
        observation_hash = payload_hash(row)
        observation_model = self.env["l10n_cl.dte.rcv.observation"]
        if observation_model.search_count(
            [("sync_id", "=", sync.id), ("payload_hash", "=", observation_hash)]
        ):
            return observation_model
        values = {name: document[name] for name in OBSERVED_FIELDS}
        values.update(
            {
                "document_id": document.id,
                "sync_id": sync.id,
                "payload_hash": observation_hash,
                "sii_state": state,
            }
        )
        return observation_model.create(values)

    # CSV

    def _csv_section(self, rows):
        """Shape CSV rows as one SII section so both sources share one path.

        A CSV has no authoritative SII summary: its summary is the sum of its
        own rows and the section stays visibly marked as ``CSV``.
        """
        details = defaultdict(list)
        for row in rows:
            number, check_digit = row["counterpart_vat"].split("-")
            detail = {key: row[field] for field, key in DETAIL_AMOUNT_KEYS.items()}
            detail.update(
                {
                    "detTipoDoc": int(row["document_type_code"]),
                    "detNroDoc": row["folio"],
                    "detFchDoc": row["document_date"] or "",
                    "detRutDoc": number,
                    "detDvDoc": check_digit,
                    "detRznSoc": row["counterpart_name"],
                    "_tax_breakdown_available": row["tax_breakdown_available"],
                }
            )
            details[row["document_type_code"]].append(detail)
        summary = []
        for type_code, items in details.items():
            summary_row = {
                key: sum(item[DETAIL_AMOUNT_KEYS[field]] for item in items)
                for field, key in SUMMARY_AMOUNT_KEYS.items()
            }
            summary_row.update(
                {"rsmnTipoDocInteger": int(type_code), "rsmnTotDoc": len(items)}
            )
            summary.append(summary_row)
        return {
            "state": CSV_SECTION_STATE,
            "summary": {"data": summary},
            "details": dict(details),
        }

    def _record_csv_errors(self, sync, errors, state):
        shown = list(errors[:MAX_STORED_ERRORS])
        if len(errors) > MAX_STORED_ERRORS:
            shown.append(
                self.env._(
                    "... and %s more invalid lines.", len(errors) - MAX_STORED_ERRORS
                )
            )
        summary = self.env._(
            "%s CSV lines were not imported; see the import errors.", len(errors)
        )
        sync.section_ids.filtered(
            lambda section: section.sii_state == CSV_SECTION_STATE
        ).write({"state": state, "error_message": summary})
        sync.write(
            {
                "error_code": "csv_invalid_rows",
                "error_message": "\n".join(str(item) for item in shown),
            }
        )
