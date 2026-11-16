# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class DteExportCatalog(models.Model):
    _name = "l10n_cl.dte.export.catalog"
    _description = "DTE Export Customs Code"
    _order = "kind, code, id"

    kind = fields.Selection(
        [
            ("sale_mode", "Sale Mode"),
            ("sale_clause", "Sale Clause"),
            ("transport", "Transport Mode"),
            ("port", "Customs Port"),
            ("package", "Package Type"),
            ("unit", "Customs Weight Unit"),
            ("payment", "Export Payment Method"),
        ],
        required=True,
        index=True,
    )
    code = fields.Char(required=True, index=True)
    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    source_url = fields.Char(required=True)
    source_date = fields.Date(required=True)

    _sql_constraints = [
        (
            "kind_code_unique",
            "unique(kind, code)",
            "The customs code must be unique per category.",
        ),
    ]

    @api.constrains("kind", "code")
    def _check_code_shape(self):
        for record in self:
            # Exportaciones: positiveInteger, totalDigits 2 (ports 4, packages 3).
            width = {"port": 4, "package": 3}.get(record.kind, 2)
            code = record.code or ""
            if not (
                0 < len(code) <= width
                and code.isascii()
                and code.isdigit()
                and int(code) > 0
            ):
                raise ValidationError(
                    _(
                        "Customs codes must be positive ASCII integers "
                        "of at most %s digits.",
                        width,
                    )
                )

    def _validate_export_code(self, kind):
        """Return a readable, active code of the expected category for snapshots."""
        self.ensure_one()
        self.check_access("read")
        if self.kind != kind or not self.active:
            raise ValidationError(
                _("Select an active customs code of the required category.")
            )
        self._check_code_shape()
        return self.code
