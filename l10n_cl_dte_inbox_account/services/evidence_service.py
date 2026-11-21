# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import binascii
import hashlib

from odoo.addons.l10n_cl_dte_inbox.services.incoming_parser import (
    SII_NS,
    extract_incoming_dtes,
    parse_incoming_dte,
)
from odoo.addons.l10n_cl_dte_inbox.services.incoming_validation import (
    IncomingValidationService,
)

from .common import (
    ADDITIONAL_TAX_CODES,
    VENDOR_DOCUMENT_CODES,
    VERSION,
    canonical_folio,
    decimal,
    diagnostic,
    reject,
    vat,
)
from .policy_service import IncomingAccountPolicyService

NS = {"s": SII_NS}


# Ordinals above the SII detail range identify global discount lines.
GLOBAL_DISCOUNT_ORDINAL = 9000


class IncomingAccountEvidenceService:
    # Document codes this reader books; bridges reading other DTEs override it.
    document_codes = VENDOR_DOCUMENT_CODES

    def __init__(self, env):
        self.env = env

    def read(self, incoming):  # pylint: disable=method-required-super
        IncomingAccountPolicyService(self.env).check_access(incoming)
        try:
            # Read only this already-authorized immutable source, not
            # arbitrary attachments.
            content = base64.b64decode(
                incoming.sudo().with_context(bin_size=False).raw_xml, validate=True
            )
        except (ValueError, TypeError, binascii.Error):
            reject(self.env, "source_digest_mismatch")
        if hashlib.sha256(content).hexdigest() != incoming.xml_sha256:
            reject(self.env, "source_digest_mismatch")
        inspection = IncomingValidationService(self.env).inspect(content)
        checks = inspection.get("documents", [])
        if any(
            inspection.get(key) != "valid"
            for key in ("schema_state", "signature_state")
        ):
            reject(self.env, "source_validation_failed")
        if len(checks) != 1 or any(
            checks[0].get(k) != "valid"
            for k in ("schema_state", "signature_state", "ted_state")
        ):
            reject(self.env, "source_validation_failed")
        document = self.extract(content)
        identity = document["identity"]
        expected = dict(
            issuer_vat=vat(incoming.issuer_vat),
            receiver_vat=vat(incoming.receiver_vat),
            document_code=incoming.document_code,
            folio=str(incoming.folio),
            issue_date=str(incoming.issue_date),
        )
        if any(identity[k] != value for k, value in expected.items()):
            reject(self.env, "identity_mismatch")
        document["xml_sha256"] = incoming.xml_sha256
        return document

    def extract(self, content):
        identity = parse_incoming_dte(content, translate=self.env._)
        _, dtes = extract_incoming_dtes(content, self.env._)
        node = dtes[0].find("s:Documento", NS)
        header = node.find("s:Encabezado", NS)
        id_doc = header.find("s:IdDoc", NS)
        totals = header.find("s:Totales", NS)
        self._check_supported_features(node)
        identity.update(
            issuer_vat=vat(identity["issuer_vat"]),
            receiver_vat=vat(identity["receiver_vat"]),
            folio=canonical_folio(identity["folio"]),
            due_date=id_doc.findtext("s:FchVenc", default="", namespaces=NS),
        )
        if (
            identity["document_code"] not in self.document_codes
            or not identity["folio"]
        ):
            reject(self.env, "unsupported_feature")
        amounts = {}
        for key, tag in (
            ("net", "MntNeto"),
            ("exempt", "MntExe"),
            ("vat_rate", "TasaIVA"),
            ("vat", "IVA"),
            ("total", "MntTotal"),
        ):
            amounts[key] = self._number(totals, tag, "0" if tag != "MntTotal" else None)
        if not amounts["total"]:
            # A note that only corrects text has no amounts to book.
            reject(self.env, "unsupported_feature")
        lines = [self._line(detail) for detail in node.findall("s:Detalle", NS)]
        if not lines:
            reject(self.env, "unsupported_feature")
        lines += [
            self._global_discount(discount)
            for discount in node.findall("s:DscRcgGlobal", NS)
        ]
        if not 1 <= len(lines) <= 2000 or len(
            {line["ordinal"] for line in lines}
        ) != len(lines):
            reject(self.env, "unsupported_feature")
        if identity["document_code"] == "34" and any(
            not line["exempt"] for line in lines
        ):
            reject(self.env, "unsupported_feature")
        if (
            any(not line["exempt"] for line in lines)
            and decimal(amounts["vat_rate"]) != 19
        ):
            reject(self.env, "unsupported_feature")
        additional = self._additional_taxes(totals)
        if additional:
            amounts["additional_taxes"] = additional
        result = dict(
            version=VERSION,
            identity=identity,
            currency="CLP",
            totals=amounts,
            lines=lines,
            unsupported=[],
        )
        if any(
            id_doc.find("s:" + tag, NS) is not None
            for tag in ("FchCancel", "MntCancel", "SaldoInsol", "MntPagos")
        ):
            result["information"] = [
                diagnostic(self.env, "payments_not_imported", severity="info")
            ]
        return result

    def _number(self, node, tag, default=None):
        value = (
            node.findtext("s:" + tag, default=default, namespaces=NS)
            if node is not None
            else default
        )
        try:
            number = decimal(value)
            if number < 0:
                raise ValueError
            return format(number, "f")
        except ValueError:
            reject(self.env, "unsupported_feature", source_value=tag)

    def _additional_code(self, node):
        codes = node.findall("s:CodImpAdic", NS)
        if len(codes) > 1 or (
            codes and codes[0].text.strip() not in ADDITIONAL_TAX_CODES
        ):
            reject(self.env, "unsupported_feature")
        return codes[0].text.strip() if codes else None

    def _additional_taxes(self, totals):
        """ImptoReten charged on top of VAT; withholdings stay unsupported."""
        result = []
        for node in totals.findall("s:ImptoReten", NS):
            code = node.findtext("s:TipoImp", default="", namespaces=NS).strip()
            if code not in ADDITIONAL_TAX_CODES:
                reject(self.env, "unsupported_feature")
            result.append(
                dict(
                    code=code,
                    rate=self._number(node, "TasaImp"),
                    amount=self._number(node, "MontoImp"),
                )
            )
        return result

    def _global_discount(self, node):
        """Read a peso global discount as one negative line to map and book."""
        number = canonical_folio(node.findtext("s:NroLinDR", namespaces=NS))
        exempt = node.findtext("s:IndExeDR", default="", namespaces=NS)
        if (
            not number
            or node.findtext("s:TpoMov", namespaces=NS) != "D"
            or node.findtext("s:TpoValor", namespaces=NS) != "$"
            or exempt not in {"", "1"}
        ):
            reject(self.env, "unsupported_feature")
        value = self._number(node, "ValorDR")
        return dict(
            # Global discounts follow the 60 SII detail lines.
            ordinal=GLOBAL_DISCOUNT_ORDINAL + int(number),
            name=node.findtext("s:GlosaDR", default="", namespaces=NS)
            or "Global discount",
            description="",
            codes=[],
            quantity="1",
            unit_price=format(-decimal(value), "f"),
            unit="",
            discount_percent="0",
            discount_amount=None,
            exempt=exempt == "1",
            amount=format(-decimal(value), "f"),
        )

    def _line(self, node):
        ordinal = canonical_folio(node.findtext("s:NroLinDet", namespaces=NS))
        exempt = node.findtext("s:IndExe", default="", namespaces=NS)
        if not ordinal or exempt not in {"", "1"}:
            reject(self.env, "unsupported_feature")
        quantity = self._number(node, "QtyItem")
        discount = self._number(node, "DescuentoPct", "0")
        if decimal(quantity) <= 0 or decimal(discount) > 100:
            reject(self.env, "unsupported_feature")
        if (
            node.find("s:DescuentoMonto", NS) is not None
            and node.find("s:DescuentoPct", NS) is None
        ):
            reject(self.env, "unsupported_feature")
        line = dict(
            ordinal=int(ordinal),
            name=node.findtext("s:NmbItem", default="", namespaces=NS),
            description=node.findtext("s:DscItem", default="", namespaces=NS),
            codes=[
                dict(
                    type=c.findtext("s:TpoCodigo", default="", namespaces=NS),
                    value=c.findtext("s:VlrCodigo", default="", namespaces=NS),
                )
                for c in node.findall("s:CdgItem", NS)
            ],
            quantity=quantity,
            unit_price=self._number(node, "PrcItem"),
            unit=node.findtext("s:UnmdItem", default="", namespaces=NS),
            discount_percent=discount,
            discount_amount=self._number(node, "DescuentoMonto")
            if node.find("s:DescuentoMonto", NS) is not None
            else None,
            exempt=exempt == "1",
            amount=self._number(node, "MontoItem"),
        )
        # Only documents with additional taxes get the key, so evidence that
        # was already bound keeps its digest.
        code = self._additional_code(node)
        if code:
            line["additional_tax_code"] = code
        return line

    def _check_supported_features(self, document):
        allowed_totals = {
            "MntNeto",
            "MntExe",
            "TasaIVA",
            "IVA",
            "ImptoReten",
            "MntTotal",
        }
        allowed_detail = {
            "NroLinDet",
            "CdgItem",
            "IndExe",
            "NmbItem",
            "DscItem",
            "QtyRef",
            "UnmdRef",
            "PrcRef",
            "QtyItem",
            "FchElabor",
            "FchVencim",
            "UnmdItem",
            "PrcItem",
            "DescuentoPct",
            "DescuentoMonto",
            # Informative foreign-currency amounts; the line stays in pesos.
            "OtrMnda",
            "CodImpAdic",
            "MontoItem",
        }
        totals = document.find("s:Encabezado/s:Totales", NS)
        if totals is None or any(
            c.tag not in {"{" + SII_NS + "}" + key for key in allowed_totals}
            for c in totals
        ):
            reject(self.env, "unsupported_feature")
        for detail in document.findall("s:Detalle", NS):
            if any(
                c.tag not in {"{" + SII_NS + "}" + key for key in allowed_detail}
                for c in detail
            ):
                reject(self.env, "unsupported_feature")
        # A national DTE states its totals in pesos; OtraMoneda only repeats
        # them in the invoice currency, so the bill is booked in CLP.
        id_doc = document.find("s:Encabezado/s:IdDoc", NS)
        if (
            id_doc.findtext("s:MntBruto", default="0", namespaces=NS) != "0"
            or id_doc.find("s:IndNoRebaja", NS) is not None
        ):
            reject(self.env, "unsupported_feature")
