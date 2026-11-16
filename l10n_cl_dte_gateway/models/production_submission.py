# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from copy import deepcopy

from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.l10n_cl_dte_signing.services.certificate_identity import normalize_rut
from odoo.addons.l10n_cl_dte_signing.services.signature_service import (
    _without_xml_declaration,
)

from ..services.document_validation import validate_document_payload
from ..services.production_gateway import ProductionSiiGateway
from ..services.production_response_parser import parse_xml
from ..services.production_transport import ProductionSiiTransport

SII = "{http://www.sii.cl/SiiDte}"
BINDING = "transport_request_artifact_id"


def _public_caf_identity(element):
    fragment = deepcopy(element)
    for node in fragment.iter():
        if isinstance(node.tag, str) and etree.QName(node).namespace == SII[1:-1]:
            node.tag = etree.QName(node).localname
    etree.cleanup_namespaces(fragment)
    return etree.tostring(fragment, method="c14n", exclusive=True, with_comments=False)


class ElectronicSubmissionProduction(models.Model):
    _inherit = "l10n_cl.electronic.submission"

    transport_request_artifact_id = fields.Many2one(
        "l10n_cl.electronic.artifact",
        string="Signed Transport Envelope",
        readonly=True,
        copy=False,
        index=True,
        check_company=True,
        ondelete="restrict",
    )
    _sql_constraints = [
        (
            "submission_transport_request_uniq",
            "unique(transport_request_artifact_id)",
            "A transport envelope can belong to only one submission.",
        )
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if any(vals.get(BINDING) for vals in vals_list) or self.env.context.get(
            "default_" + BINDING
        ):
            raise UserError(
                _("Prepare the transport envelope through the submission workflow.")
            )
        return super().create([{**vals, BINDING: False} for vals in vals_list])

    def _lock_production_chain(self):
        self.ensure_one()
        self.document_id._lock_fiscal_chain()
        self.flush_recordset()
        self.env.cr.execute(
            "SELECT id FROM l10n_cl_electronic_submission WHERE id = %s FOR UPDATE",
            [self.id],
        )
        self.invalidate_recordset()

    def _check_request_preparation(self):
        if (
            self.environment != "production"
            or self.document_id.sii_environment != "production"
            or self.company_id.dte_sii_environment != "production"
            or self.state != "pending"
            or self.attempt_ids.filtered(lambda attempt: attempt.operation == "send")
        ):
            raise UserError(
                _(
                    "Only pending Production submissions without a send attempt "
                    "can bind an envelope."
                )
            )

    def _write_internal(self, values):
        """Dormant binding primitive; caller must verify fiscal envelope content."""
        if BINDING not in values:
            return super()._write_internal(values)
        if not self.env.su:
            raise AccessError(
                _("Transport binding requires an authorized internal workflow.")
            )
        self._lock_production_chain()
        self._check_request_preparation()
        if (
            set(values) != {BINDING}
            or self.transport_request_artifact_id
            or not values[BINDING]
        ):
            raise UserError(
                _("A transport envelope must be bound once, without other changes.")
            )
        artifact = (
            self.env["l10n_cl.electronic.artifact"].browse(values[BINDING]).exists()
        )
        if (
            len(artifact) != 1
            or artifact.document_id != self.document_id
            or artifact.company_id != self.company_id
            or artifact.role != "sii_request"
        ):
            raise ValidationError(
                _("The transport envelope must be request evidence for this DTE.")
            )
        artifact.with_context(bin_size=False).action_verify_integrity()
        return super()._write_internal(values)

    def _check_production_operation_access(self):
        self.ensure_one()
        self.check_access("read")
        self.document_id.check_access("read")
        if self.env.su:
            return
        group = (
            "group_dte_manager"
            if self.document_id.document_code in {"56", "61"}
            else "group_dte_user"
        )
        if (
            self.company_id not in self.env.user.company_ids
            or self.company_id not in self.env.companies
            or not self.env.user.has_group("l10n_cl_dte_core." + group)
        ):
            raise AccessError(
                _("The DTE company and role must be authorized and enabled.")
            )
        source = self._production_source()
        source.check_access("read")
        check = getattr(source, "_check_dte_source_access", None)
        if check is not None:
            check(self.document_id.document_code)
        else:
            source.check_access("write")

    def _production_source(self):
        """Resolve one typed source without importing the downstream issuance addon."""
        registry = "l10n_cl.dte.source.registry"
        relations = (
            self.env[registry]._dte_source_relation_fields()
            if registry in self.env
            else ()
        )
        sources = [
            self.document_id[name] for name in relations if self.document_id[name]
        ]
        if len(sources) != 1:
            raise AccessError(_("A unique authorized DTE source is required."))
        return sources[0]

    def _prepare_production_request(self):
        """Dormant until enqueue and durable dispatch are activated together."""
        self._check_production_operation_access()
        with self.env.cr.savepoint():
            self._lock_production_chain()
            self._check_production_operation_access()
            self._check_request_preparation()
            submission = self.sudo().with_context(bin_size=False)
            if submission.transport_request_artifact_id:
                submission._verify_production_request()
                return submission.transport_request_artifact_id
            signed = submission.document_id._get_signed_artifact()
            signed.action_verify_integrity()
            raw = ProductionSiiGateway().prepare_document(
                submission.company_id,
                submission.environment,
                base64.b64decode(signed.content, validate=True),
            )
            artifact = submission._create_response_artifact("sii_request", raw)
            submission._write_internal({BINDING: artifact.id})
            submission._verify_production_request()
            return artifact

    def _verify_production_request(self):
        """Verify retained evidence; never reconstruct or regenerate the request."""
        self._check_production_operation_access()
        submission = self.sudo().with_context(bin_size=False)
        document = submission.document_id
        artifact = submission.transport_request_artifact_id
        if (
            not artifact
            or artifact.document_id != document
            or artifact.company_id != submission.company_id
            or artifact.role != "sii_request"
        ):
            raise ValidationError(
                _("The transport envelope must be request evidence for this DTE.")
            )
        if submission.environment != document.sii_environment:
            raise ValidationError(_("The submission must retain its DTE environment."))
        artifact.action_verify_integrity()
        signed = document._get_signed_artifact()
        signed.action_verify_integrity()
        original = base64.b64decode(signed.content, validate=True)
        raw = base64.b64decode(artifact.content, validate=True)
        ProductionSiiTransport().validate_request(
            submission.company_id, submission.environment, raw
        )
        payload = validate_document_payload(
            original,
            submission._get_document_validator,
            allowed_codes={document.document_code},
            strict_sii=True,
        )
        header = payload.find(SII + "Encabezado")
        dd = payload.find(f"{SII}TED/{SII}DD")
        issuer = normalize_rut(submission.company_id.vat)
        if (
            header.findtext(f"{SII}IdDoc/{SII}Folio") != str(document.folio)
            or normalize_rut(header.findtext(f"{SII}Emisor/{SII}RUTEmisor")) != issuer
            or dd.findtext(SII + "F") != str(document.folio)
            or dd.findtext(SII + "TD") != document.document_code
            or normalize_rut(dd.findtext(SII + "RE")) != issuer
        ):
            raise ValidationError(
                _("The signed DTE identity differs from the submission.")
            )
        dtes = parse_xml(raw).findall(f"{SII}SetDTE/{SII}DTE")
        if (
            len(dtes) != 1
            or _without_xml_declaration(original) not in raw
            or etree.tostring(dtes[0], method="c14n", exclusive=True)
            != etree.tostring(payload.getparent(), method="c14n", exclusive=True)
        ):
            raise ValidationError(
                _("The envelope must contain this exact signed DTE only.")
            )
        folio = submission.env["l10n_cl.caf.folio"].search(
            [("document_id", "=", document.id)]
        )
        caf = folio.authorization_id
        if (
            len(folio) != 1
            or folio.state == "void"
            or folio.number != document.folio
            or caf.is_synthetic
            or caf.sii_environment != "production"
            or caf.company_id != submission.company_id
            or caf.document_code != document.document_code
        ):
            raise ValidationError(
                _("The request requires its non-synthetic Production CAF.")
            )
        caf._check_caf_integrity()
        embedded = payload.findall(f"{SII}TED/{SII}DD/{SII}CAF")
        reserved = parse_xml(base64.b64decode(caf.caf_file, validate=True)).find("CAF")
        if (
            len(embedded) != 1
            or reserved is None
            or _public_caf_identity(embedded[0]) != _public_caf_identity(reserved)
        ):
            raise ValidationError(
                _("The signed TED must contain the reserved Production CAF.")
            )
        return raw
