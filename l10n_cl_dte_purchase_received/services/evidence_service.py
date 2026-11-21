# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from copy import deepcopy

from odoo.addons.l10n_cl_dte_inbox.services.incoming_parser import (
    extract_incoming_dtes,
)
from odoo.addons.l10n_cl_dte_inbox_account.services.common import decimal
from odoo.addons.l10n_cl_dte_inbox_account.services.evidence_service import (
    NS,
    IncomingAccountEvidenceService,
)

from .common import (
    PARTIAL_RETENTION_RATES,
    PRODUCT_RETENTION_CODES,
    PURCHASE_CODE,
    TOTAL_RETENTION_CODE,
    TOTAL_RETENTION_RATE,
    reject_received,
)


class ReceivedPurchaseEvidenceService(IncomingAccountEvidenceService):
    """Read a signed DTE 46 whose buyer retained all or part of our VAT."""

    document_codes = frozenset({PURCHASE_CODE})

    def extract(self, content):
        document = super().extract(content)
        _root, dtes = extract_incoming_dtes(content, self.env._)
        totals = dtes[0].find("s:Documento/s:Encabezado/s:Totales", NS)
        retentions = [
            dict(
                code=node.findtext("s:TipoImp", default="", namespaces=NS).strip(),
                rate=self._number(node, "TasaImp"),
                amount=self._number(node, "MontoImp"),
            )
            for node in totals.findall("s:ImptoReten", NS)
        ]
        amounts = document["totals"]
        if totals.find("s:IVANoRet", NS) is not None:
            # Only documents with a partial retention get the key, so bound
            # total-retention evidence keeps its digest.
            amounts["vat_not_retained"] = self._number(totals, "IVANoRet")
        self._check_retentions(amounts, retentions)
        self._check_line_codes(document["lines"], retentions)
        amounts["retentions"] = retentions
        return document

    def _check_retentions(self, amounts, retentions):
        vat_amount = decimal(amounts["vat"])
        codes = [item["code"] for item in retentions]
        retained = sum(decimal(item["amount"]) for item in retentions)
        rates = {**PARTIAL_RETENTION_RATES, TOTAL_RETENTION_CODE: TOTAL_RETENTION_RATE}
        if (
            bool(vat_amount) != bool(retentions)
            or len(set(codes)) != len(codes)
            or any(
                decimal(item["rate"]) != rates[item["code"]]
                or not decimal(item["amount"])
                for item in retentions
            )
            or retained > vat_amount
        ):
            reject_received(self.env, "retention_unsupported")
        if all(rates[code] == TOTAL_RETENTION_RATE for code in codes) and (
            retained != vat_amount
        ):
            reject_received(self.env, "retention_unsupported")
        not_retained = decimal(amounts.get("vat_not_retained", "0"))
        if not_retained != vat_amount - retained:
            reject_received(
                self.env,
                "amount_mismatch",
                source_value=not_retained,
                odoo_value=vat_amount - retained,
            )
        receivable = (
            decimal(amounts["net"]) + decimal(amounts["exempt"]) + vat_amount - retained
        )
        if receivable != decimal(amounts["total"]):
            reject_received(
                self.env,
                "amount_mismatch",
                source_value=amounts["total"],
                odoo_value=receivable,
            )

    def _check_line_codes(self, lines, retentions):
        """Each code in Totales is declared by a taxable detail, and back."""
        declared = {
            line.get("retention_code", TOTAL_RETENTION_CODE)
            for line in lines
            if not line["exempt"]
        }
        if retentions and declared != {item["code"] for item in retentions}:
            reject_received(self.env, "retention_unsupported")

    def _check_supported_features(self, document):
        totals = document.find("s:Encabezado/s:Totales", NS)
        codes = [
            node.findtext("s:TipoImp", default="", namespaces=NS).strip()
            for node in (
                totals.findall("s:ImptoReten", NS) if totals is not None else []
            )
        ] + [
            (node.text or "").strip()
            for node in document.findall("s:Detalle/s:CodImpAdic", NS)
        ]
        for code in codes:
            if code in PRODUCT_RETENTION_CODES and code not in PARTIAL_RETENTION_RATES:
                reject_received(self.env, "retention_partial", source_value=code)
            if code != TOTAL_RETENTION_CODE and code not in PARTIAL_RETENTION_RATES:
                reject_received(self.env, "retention_unsupported", source_value=code)
        return super()._check_supported_features(
            self._without_vat_not_retained(document)
        )

    @staticmethod
    def _without_vat_not_retained(document):
        """IVANoRet only restates VAT minus retentions; extract() checks it."""
        node = document.find("s:Encabezado/s:Totales/s:IVANoRet", NS)
        if node is None:
            return document
        document = deepcopy(document)
        node = document.find("s:Encabezado/s:Totales/s:IVANoRet", NS)
        node.getparent().remove(node)
        return document

    def _line(self, node):
        line = super()._line(node)
        codes = node.findall("s:CodImpAdic", NS)
        if len(codes) > 1:
            reject_received(self.env, "retention_unsupported")
        code = (codes[0].text or "").strip() if codes else ""
        if code and code != TOTAL_RETENTION_CODE:
            # Only partial details get the key, so bound evidence of a total
            # retention keeps its digest.
            line["retention_code"] = code
        return line

    def _additional_code(self, node):
        # Retention codes on a detail are read by _line(); none is charged.
        return None

    def _additional_taxes(self, totals):
        # The retained VAT is not charged on top; extract() reads it.
        return []
