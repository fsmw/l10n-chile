# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class DteCertificationSetCaf(models.Model):
    _name = "l10n_cl.dte.certification.set.caf"
    _description = "Certification Set CAF"
    _order = "document_code, sequence, caf_id"
    _check_company_auto = True
    _sql_constraints = [
        (
            "set_caf_uniq",
            "unique(set_id, caf_id)",
            "A CAF can only be added once to a certification set.",
        ),
    ]

    set_id = fields.Many2one(
        "l10n_cl.dte.certification.set",
        required=True,
        index=True,
        ondelete="cascade",
        check_company=True,
    )
    caf_id = fields.Many2one(
        "l10n_cl.caf.authorization",
        required=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    company_id = fields.Many2one(
        related="set_id.company_id", store=True, index=True, readonly=True
    )
    document_code = fields.Char(
        related="caf_id.document_code", store=True, index=True, readonly=True
    )
    folio_start = fields.Integer(
        related="caf_id.folio_start", store=True, readonly=True
    )
    folio_end = fields.Integer(related="caf_id.folio_end", store=True, readonly=True)
    date_from = fields.Date(related="caf_id.date_from", store=True, readonly=True)
    date_to = fields.Date(related="caf_id.date_to", store=True, readonly=True)
    caf_state = fields.Selection(related="caf_id.state", store=True, readonly=True)
    caf_digest = fields.Char(related="caf_id.caf_digest", store=True, readonly=True)
    sequence = fields.Integer(required=True, default=10)
    role = fields.Selection(
        [("primary", "Primary"), ("additional", "Additional")],
        required=True,
        default="additional",
    )

    @api.constrains("set_id", "caf_id", "role")
    def _check_caf_eligibility(self):
        for line in self:
            caf = line.caf_id
            if (
                caf.company_id != line.set_id.company_id
                or caf.sii_environment != "certification"
                or caf.is_synthetic
                or caf.state not in {"validated", "exhausted"}
            ):
                raise ValidationError(
                    _(
                        "Only a validated, non-synthetic Certification CAF for "
                        "the set company can be linked."
                    )
                )
            case_codes = set(line.set_id.case_ids.mapped("document_code"))
            if caf.document_code not in {"33", "34", "56", "61"} or (
                caf.document_code not in case_codes
            ):
                raise ValidationError(
                    _("The CAF document type must be supported by a case in the set.")
                )
            content = caf._decode_content(caf.caf_file)
            parser = etree.XMLParser(
                resolve_entities=False, no_network=True, load_dtd=False
            )
            try:
                root = etree.fromstring(content, parser=parser)
                key_id = root.xpath(
                    "string(./*[local-name()='CAF']/*[local-name()='DA']/*[local-name()='IDK'])"
                )
                issuer_vat = root.xpath(
                    "string(./*[local-name()='CAF']/*[local-name()='DA']/*[local-name()='RE'])"
                )
                document_code = root.xpath(
                    "string(./*[local-name()='CAF']/*[local-name()='DA']/*[local-name()='TD'])"
                )
            except (etree.XMLSyntaxError, ValueError) as error:
                raise ValidationError(_("The linked CAF XML is invalid.")) from error

            def normalize_vat(value):
                return "".join(
                    char for char in value.upper() if char.isdigit() or char == "K"
                )

            if (
                etree.QName(root).localname != "AUTORIZACION"
                or key_id.strip() != "100"
                or normalize_vat(issuer_vat)
                != normalize_vat(line.set_id.company_id.vat or "")
                or document_code.strip() != caf.document_code
            ):
                raise ValidationError(
                    _(
                        "The linked CAF must match Certification, the set company "
                        "and document type."
                    )
                )
            if line.role == "primary" and self.search_count(
                [
                    ("id", "!=", line.id),
                    ("set_id", "=", line.set_id.id),
                    ("document_code", "=", line.document_code),
                    ("role", "=", "primary"),
                ]
            ):
                raise ValidationError(
                    _("A set can have only one primary CAF per document type.")
                )

    @api.model_create_multi
    def create(self, vals_list):
        for set_id in sorted({values["set_id"] for values in vals_list}):
            self.env["l10n_cl.dte.certification.set"].browse(
                set_id
            )._check_can_add_caf()
        for values in vals_list:
            certification_set = self.env["l10n_cl.dte.certification.set"].browse(
                values["set_id"]
            )
            if (
                not certification_set.can_edit_envelope
                and values.get("role", "additional") != "additional"
            ):
                raise UserError(
                    _("Only additional CAFs can be added to a new revision.")
                )
        lines = super().create(vals_list)
        return lines

    def write(self, vals):
        if not set(vals) <= {"sequence", "role"}:
            raise UserError(_("CAF links can only be reordered or assigned a role."))
        self._check_eligibility_access()
        return super().write(vals)

    def unlink(self):
        self._check_eligibility_access()
        return super().unlink()

    def _check_eligibility_access(self):
        for line in self:
            line.set_id._check_certification_access()
            if not line.set_id.can_edit_envelope:
                raise UserError(
                    _("CAF configuration is frozen after document generation begins.")
                )
