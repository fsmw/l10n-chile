# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hashlib
import json

from odoo import api, fields, models

SNAPSHOT_SCHEMA = "dte52-gd-snapshot-v1"


class DteDispatchSnapshot(models.AbstractModel):
    _name = "l10n_cl.dte.dispatch.snapshot"
    _description = "Dispatch fiscal snapshot builder"

    @api.model
    def capture(self, dispatch):
        """Return the normalized fiscal values of a dispatch as a plain dict.

        Only values are kept, never database ids, so the digest depends on
        the content alone. Lines are ordered by sequence, then by id.
        """
        lines = dispatch.line_ids.sorted(lambda line: (line.sequence, line.id))
        return {
            "schema": SNAPSHOT_SCHEMA,
            "name": dispatch.name,
            "sii_environment": dispatch.sii_environment,
            "source_kind": dispatch.source_kind,
            "source_key": dispatch.source_key,
            "issue_date": fields.Date.to_string(dispatch.issue_date),
            "partner": {
                "name": dispatch.partner_id.name,
                "vat": dispatch.partner_id.vat or "",
            },
            "transfer_indicator": dispatch.transfer_indicator,
            "dispatch_type_code": dispatch.dispatch_type_code,
            "origin_address": dispatch.origin_address,
            "destination_address": dispatch.destination_address,
            "currency": dispatch.currency_id.name,
            "lines": [
                {
                    "description": line.description,
                    "quantity": line.quantity,
                    "uom": line.uom_id.name,
                    "unit_price": line.unit_price,
                    "tax_category": line.tax_category,
                }
                for line in lines
            ],
        }

    @api.model
    def serialize(self, payload):
        """Return the canonical JSON text of a snapshot payload."""
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

    @api.model
    def digest(self, serialized):
        """Return the SHA-256 hex digest of a canonical snapshot text."""
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
