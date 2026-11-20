# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models

from odoo.addons.l10n_cl_dte_note.services.note_reference_service import (
    NoteReferenceService,
)

EXPORT_CODES = frozenset({"110", "111", "112"})
# Snapshot customs keys printed with the catalog category they belong to.
CATALOG_ROWS = (
    ("sale_mode", "sale_mode", "Modalidad de venta"),
    ("sale_clause", "sale_clause", "Cláusula de venta"),
    ("transport_mode", "transport", "Vía de transporte"),
    ("loading_port", "port", "Puerto de embarque"),
    ("unloading_port", "port", "Puerto de desembarque"),
)
TEXT_ROWS = (
    ("clause_total", "Total cláusula"),
    ("transport_name", "Nave o vehículo"),
    ("carrier_name", "Transportista"),
    ("booking", "Booking"),
    ("gross_weight", "Peso bruto"),
    ("net_weight", "Peso neto"),
    ("total_packages", "Total bultos"),
    ("freight", "Flete"),
    ("insurance", "Seguro"),
)


class AccountMove(models.Model):
    _inherit = "account.move"

    def _get_dte_export_print_values(self):
        """Export data of the printed DTE, read from its immutable snapshot."""
        self.ensure_one()
        document = self._get_dte_print_document()
        if not document or document.document_code not in EXPORT_CODES:
            return {}
        snapshot = NoteReferenceService(self.env).read_original_snapshot(document)
        customs = snapshot.get("customs", {})
        other = snapshot.get("other_currency", {})
        return {
            "currency": snapshot["currency"]["sii_name"],
            "total": snapshot["totals"]["total"],
            "rate": other.get("rate") if other.get("total") != "0" else None,
            "clp_total": other.get("total") if other.get("total") != "0" else None,
            "rows": self._export_customs_rows(customs),
            "destination": self._export_country(customs.get("destination_country")),
            "recipient_country": self._export_country(customs.get("recipient_country")),
            "references": snapshot.get("references", []),
        }

    def _export_customs_rows(self, customs):
        catalog = (
            self.env["l10n_cl.dte.export.catalog"]
            .sudo()
            .with_context(active_test=False)
        )
        rows = []
        for key, kind, label in CATALOG_ROWS:
            code = customs.get(key)
            if code:
                entry = catalog.search([("kind", "=", kind), ("code", "=", code)])
                rows.append((label, f"{code} - {entry.name}" if entry else code))
        rows += [(label, customs[key]) for key, label in TEXT_ROWS if customs.get(key)]
        return rows

    def _export_country(self, customs_code):
        if not customs_code:
            return None
        country = self.env["res.country"].search(
            [("l10n_cl_customs_code", "=", customs_code)], limit=1
        )
        return country.name or customs_code
