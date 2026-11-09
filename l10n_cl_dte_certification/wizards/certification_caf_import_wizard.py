# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import binascii
import hashlib
import re
from pathlib import PurePath

from lxml import etree

from odoo import _, fields, models
from odoo.exceptions import UserError, ValidationError


class CertificationCafImportWizard(models.TransientModel):
    _name = "l10n_cl.dte.certification.caf.import.wizard"
    _description = "Import Certification CAF"

    set_id = fields.Many2one(
        "l10n_cl.dte.certification.set", required=True, readonly=True
    )
    caf_file = fields.Binary(required=True, attachment=False)
    caf_filename = fields.Char(required=True)

    def action_import(self):
        self.ensure_one()
        certification_set = self.set_id
        certification_set._check_can_add_caf()
        certification_set._verify_source_integrity()
        if (
            certification_set.company_id.dte_sii_environment != "certification"
            or not certification_set.company_id.dte_certification_enabled
        ):
            raise UserError(
                _("CAF import is available only for enabled Certification sets.")
            )
        try:
            content = base64.b64decode(self.caf_file, validate=True)
            if len(content) > 1024 * 1024:
                raise ValueError("CAF too large")
            if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
                raise ValueError("DTD is forbidden")
            parser = etree.XMLParser(
                resolve_entities=False, no_network=True, load_dtd=False
            )
            root = etree.fromstring(content, parser=parser)
            if etree.QName(root).localname != "AUTORIZACION":
                raise ValueError("CAF authorization data is missing")
            data_nodes = root.xpath("./*[local-name()='CAF']/*[local-name()='DA']")
            if len(data_nodes) != 1:
                raise ValueError("CAF authorization data is missing")
            data = data_nodes[0]
            key_id = data.xpath("string(./*[local-name()='IDK'])").strip()
            if key_id != "100":
                raise ValidationError(
                    _(
                        "Only CAF files from SII Certification (IDK 100) can be "
                        "imported."
                    )
                )
            filename = PurePath(self.caf_filename or "caf.xml").name[:255]
            values = {
                "document_code": data.xpath("string(./*[local-name()='TD'])").strip(),
                "issuer_vat": data.xpath("string(./*[local-name()='RE'])").strip(),
                "folio_start": int(
                    data.xpath("string(./*[local-name()='RNG']/*[local-name()='D'])")
                ),
                "folio_end": int(
                    data.xpath("string(./*[local-name()='RNG']/*[local-name()='H'])")
                ),
                "date_from": data.xpath("string(./*[local-name()='FA'])").strip(),
            }
        except (
            binascii.Error,
            TypeError,
            ValueError,
            etree.XMLSyntaxError,
        ) as error:
            raise ValidationError(
                _("The uploaded file is not a valid CAF XML.")
            ) from error
        if values["folio_start"] < 1 or values["folio_end"] < values["folio_start"]:
            raise ValidationError(_("The CAF folio range is invalid."))
        set_document_codes = {case.document_code for case in certification_set.case_ids}
        if (
            values["document_code"] not in {"33", "34", "56", "61"}
            or values["document_code"] not in set_document_codes
        ):
            raise ValidationError(
                _("This CAF document type is not present in the set.")
            )
        normalized_issuer = re.sub(r"[^0-9Kk]", "", values["issuer_vat"]).upper()
        normalized_company = re.sub(
            r"[^0-9Kk]", "", certification_set.company_id.vat or ""
        ).upper()
        if not normalized_company or normalized_issuer != normalized_company:
            raise ValidationError(_("The CAF issuer must match the set company."))
        authorization_model = self.env["l10n_cl.caf.authorization"]
        authorization = authorization_model.search(
            [
                ("company_id", "=", certification_set.company_id.id),
                ("caf_digest", "=", hashlib.sha256(content).hexdigest()),
            ],
            limit=1,
        )
        if authorization:
            if (
                authorization.sii_environment != "certification"
                or authorization.is_synthetic
                or authorization.state not in {"draft", "validated", "exhausted"}
            ):
                raise UserError(
                    _("The registered CAF is not eligible for Certification use.")
                )
            metadata = authorization._parse_and_verify_caf()
            authorization._check_metadata_matches(metadata)
            if authorization.state == "draft":
                authorization.action_validate_authorization()
        else:
            authorization = authorization_model.create(
                {
                    **values,
                    "name": filename,
                    "company_id": certification_set.company_id.id,
                    "sii_environment": "certification",
                    "caf_file": self.caf_file,
                    "caf_filename": filename,
                }
            )
            authorization.action_validate_authorization()
        if authorization in certification_set.caf_line_ids.caf_id:
            return self._already_linked_notification(authorization)
        same_type = certification_set.caf_line_ids.filtered(
            lambda relation: relation.document_code == values["document_code"]
        )
        self.env["l10n_cl.dte.certification.set.caf"].create(
            {
                "set_id": certification_set.id,
                "caf_id": authorization.id,
                "role": "additional"
                if same_type or not certification_set.can_edit_envelope
                else "primary",
                "sequence": max(same_type.mapped("sequence"), default=0) + 10,
            }
        )
        return {"type": "ir.actions.act_window_close"}

    def _already_linked_notification(self, authorization):
        shortages = [
            item["message"]
            for item in self.set_id._get_folio_requirements()
            if item["missing"]
        ]
        message = _(
            "This CAF is already linked to the set: DTE %(code)s, "
            "folios %(start)s-%(end)s.",
            code=authorization.document_code,
            start=authorization.folio_start,
            end=authorization.folio_end,
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("CAF already linked"),
                "message": "\n".join([message, *shortages]),
                "type": "warning" if shortages else "info",
                "sticky": True,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
