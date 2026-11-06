# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import binascii
import hashlib

from psycopg2 import IntegrityError

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class DteCertificationSet(models.Model):
    _name = "l10n_cl.dte.certification.set"
    _description = "SII Certification Set Source"
    _order = "create_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, readonly=True)
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, index=True
    )
    source_file = fields.Binary(
        required=True,
        attachment=True,
        readonly=True,
        copy=False,
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    source_filename = fields.Char(required=True, readonly=True, copy=False)
    source_digest = fields.Char(required=True, readonly=True, index=True, copy=False)
    source_byte_count = fields.Integer(required=True, readonly=True)
    imported_by = fields.Many2one(
        "res.users", required=True, readonly=True, default=lambda self: self.env.user
    )
    parse_state = fields.Selection(
        [("pending", "Pending"), ("parsed", "Parsed"), ("blocked", "Blocked")],
        required=True,
        default="pending",
        readonly=True,
    )
    parser_version = fields.Char(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    parsed_structure = fields.Json(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    parse_message = fields.Text(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )

    _sql_constraints = [
        (
            "certification_set_source_uniq",
            "unique(company_id, source_digest)",
            "This source file is already registered for the company.",
        ),
    ]

    @api.model
    def _create_from_import(self, company, filename, encoded_source):
        try:
            source = base64.b64decode(encoded_source, validate=True)
        except (binascii.Error, TypeError, ValueError) as error:
            raise ValidationError(_("Upload a valid TXT file.")) from error
        if not filename or not filename.lower().endswith(".txt"):
            raise ValidationError(_("The original SII set must be a TXT file."))
        if not source or len(source) > 2 * 1024 * 1024:
            raise ValidationError(
                _("The TXT file must contain data and cannot exceed 2 MiB.")
            )
        if not company or company.id not in self.env.companies.ids:
            raise ValidationError(_("Select a company available to your user."))
        digest = hashlib.sha256(source).hexdigest()
        domain = [("company_id", "=", company.id), ("source_digest", "=", digest)]
        if self.sudo().search_count(domain):
            raise ValidationError(
                _("This source file is already registered for the company.")
            )
        values = {
            "name": filename.rsplit(".", 1)[0],
            "company_id": company.id,
            "source_file": encoded_source,
            "source_filename": filename,
            "source_digest": digest,
            "source_byte_count": len(source),
            "imported_by": self.env.user.id,
        }
        try:
            with self.env.cr.savepoint():
                return super(DteCertificationSet, self.sudo()).create(values)
        except IntegrityError as error:
            raise ValidationError(
                _("This source file is already registered for the company.")
            ) from error

    def action_parse_source(self):
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise ValidationError(_("Only DTE managers can parse a certification set."))
        from ..services.set_structure_parser import (
            PARSER_VERSION,
            CertificationSetStructureParser,
        )

        for record in self:
            source = base64.b64decode(record.source_file)
            try:
                structure = CertificationSetStructureParser(record.env).parse(source)
            except ValidationError as error:
                record.sudo().write(
                    {
                        "parse_state": "blocked",
                        "parser_version": PARSER_VERSION,
                        "parsed_structure": False,
                        "parse_message": str(error),
                    }
                )
            else:
                has_blockers = bool(structure["diagnostics"])
                record.sudo().write(
                    {
                        "parse_state": "blocked" if has_blockers else "parsed",
                        "parser_version": PARSER_VERSION,
                        "parsed_structure": structure,
                        "parse_message": "\n".join(
                            f"{item['code']}: {item['case']} "
                            f"({item['source_text']})"
                            for item in structure["diagnostics"]
                        )
                        or False,
                    }
                )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("SET analysis complete"),
                "message": _(
                    "Review the detected sections, cases, and any format block."
                ),
                "type": "success"
                if all(record.parse_state == "parsed" for record in self)
                else "warning",
                "sticky": False,
            },
        }
