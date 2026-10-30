# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class L10nClDteCaf(models.Model):
    _name = "l10n_cl.dte.caf"
    _description = "Rango de folios autorizado por el SII"
    _order = "company_id, document_type_id, start_nb"

    name = fields.Char(string="Archivo CAF", required=True, copy=False)
    company_id = fields.Many2one(
        "res.company",
        string="Compañía",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    document_type_id = fields.Many2one(
        "l10n_latam.document.type", string="Tipo de documento", required=True
    )
    start_nb = fields.Integer(string="Folio inicial", required=True, readonly=True)
    final_nb = fields.Integer(string="Folio final", required=True, readonly=True)
    next_folio = fields.Integer(
        string="Próximo folio", required=True, readonly=True, copy=False
    )
    state = fields.Selection(
        [
            ("uploaded", "Cargado"),
            ("in_use", "En uso"),
            ("exhausted", "Agotado"),
            ("disabled", "Desactivado"),
        ],
        string="Estado CAF",
        default="uploaded",
        required=True,
        copy=False,
    )
    _sql_constraints = [
        (
            "caf_range_valid",
            "CHECK(start_nb > 0 AND final_nb >= start_nb)",
            "Invalid CAF folio range.",
        ),
    ]

    @api.model
    def create_from_xml(self, filename, payload, company):
        """Parse CAF metadata; keep its private key in protected storage only."""
        try:
            root = etree.fromstring(
                payload,
                parser=etree.XMLParser(
                    resolve_entities=False,
                    no_network=True,
                    load_dtd=False,
                    huge_tree=False,
                ),
            )
            da = root.xpath(
                "//*[local-name()='AUTORIZACION']/*[local-name()='CAF']/*[local-name()='DA']"
            )[0]
            code = da.xpath("./*[local-name()='TD']/text()")[0]
            start = int(
                da.xpath("./*[local-name()='RNG']/*[local-name()='D']/text()")[0]
            )
            end = int(da.xpath("./*[local-name()='RNG']/*[local-name()='H']/text()")[0])
            issuer = da.xpath("./*[local-name()='RE']/text()")[0]
        except (ValueError, IndexError, etree.XMLSyntaxError) as error:
            raise ValidationError(
                _("The uploaded file is not a valid SII CAF.")
            ) from error
        document_type = self.env["l10n_latam.document.type"].search(
            [("country_id.code", "=", "CL"), ("code", "=", code)], limit=1
        )
        if not document_type or end < start:
            raise ValidationError(_("CAF document type or range is invalid."))
        if _normalize_rut(issuer) != _normalize_rut(company.vat):
            raise ValidationError(
                _("CAF issuer RUT does not match the selected company.")
            )
        values = {
            "name": filename,
            "company_id": company.id,
            "document_type_id": document_type.id,
            "start_nb": start,
            "final_nb": end,
            "next_folio": start,
            "state": "uploaded",
            "caf_data": base64.b64encode(payload),
        }
        return self.create(values)

    caf_data = fields.Binary(
        required=True,
        attachment=True,
        groups="l10n_cl_dte_sii.group_dte_manager",
        copy=False,
    )

    def _decode_caf(self):
        self.ensure_one()
        try:
            payload = base64.b64decode(self.with_context(bin_size=False).caf_data)
            parser = etree.XMLParser(resolve_entities=False, no_network=True)
            return etree.fromstring(payload, parser=parser)
        except (ValueError, TypeError, etree.XMLSyntaxError) as error:
            raise ValidationError(_("The stored CAF XML is invalid.")) from error

    @api.model
    def _get_caf_file(self, company_id, document_type_id, folio):
        caf = self.sudo().search(
            [
                ("company_id", "=", company_id),
                ("document_type_id", "=", document_type_id),
                ("state", "=", "in_use"),
                ("start_nb", "<=", folio),
                ("final_nb", ">=", folio),
            ],
            limit=1,
        )
        if not caf:
            raise UserError(
                _(
                    "No active CAF covers folio %(folio)s for this document type.",
                    folio=folio,
                )
            )
        return caf._decode_caf()

    def action_enable(self):
        for caf in self:
            caf._check_overlap()
            caf.state = "in_use"
        return True

    def action_disable(self):
        self.write({"state": "disabled"})
        return True

    def _check_overlap(self):
        self.ensure_one()
        overlap = self.search_count(
            [
                ("id", "!=", self.id),
                ("company_id", "=", self.company_id.id),
                ("document_type_id", "=", self.document_type_id.id),
                ("state", "=", "in_use"),
                ("start_nb", "<=", self.final_nb),
                ("final_nb", ">=", self.start_nb),
            ]
        )
        if overlap:
            raise ValidationError(
                _("An active CAF already covers part of this folio range.")
            )

    def _reserve_next_folio(self):
        self.ensure_one()
        self.env.cr.execute(
            "SELECT id FROM l10n_cl_dte_caf WHERE id = %s FOR UPDATE", [self.id]
        )
        self.invalidate_recordset(["next_folio", "state"])
        if self.state != "in_use" or self.next_folio > self.final_nb:
            raise UserError(
                _("There are no active authorized folios for this document type.")
            )
        folio = self.next_folio
        while folio <= self.final_nb and self._folio_is_already_used(folio):
            folio += 1
        if folio > self.final_nb:
            raise UserError(
                _(
                    "The active CAF range contains only folios already used by "
                    "issued documents."
                )
            )
        next_folio = folio + 1
        self.write(
            {
                "next_folio": next_folio,
                "state": "exhausted" if next_folio > self.final_nb else "in_use",
            }
        )
        return folio

    def _folio_is_already_used(self, folio):
        self.ensure_one()
        number = str(folio)
        self.env.cr.execute(
            """
            SELECT name
              FROM account_move
             WHERE company_id = %s
               AND l10n_latam_document_type_id = %s
               AND move_type IN ('out_invoice', 'out_refund', 'out_receipt')
               AND name IS NOT NULL AND name != '/' AND name != ''
            """,
            [self.company_id.id, self.document_type_id.id],
        )
        for (name,) in self.env.cr.fetchall():
            suffix = name.rsplit(" ", 1)[-1]
            if suffix.isdigit() and int(suffix) == int(number):
                return True
        if "l10n_cl.delivery.guide" in self.env.registry.models:
            return bool(
                self.env["l10n_cl.delivery.guide"]
                .sudo()
                .search_count(
                    [
                        ("company_id", "=", self.company_id.id),
                        ("direction", "=", "outgoing"),
                        (
                            "l10n_latam_document_type_id",
                            "=",
                            self.document_type_id.id,
                        ),
                        ("l10n_latam_document_number", "=", number),
                    ],
                    limit=1,
                )
            )
        return False


def _normalize_rut(value):
    return (value or "").replace(".", "").replace("-", "").replace(" ", "").upper()
