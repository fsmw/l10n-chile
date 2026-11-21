# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.addons.l10n_cl_dte_inbox.services.incoming_parser import (
    extract_incoming_dtes,
)
from odoo.addons.l10n_cl_dte_inbox_account.services.common import decimal
from odoo.addons.l10n_cl_dte_inbox_account.services.evidence_service import (
    NS,
    IncomingAccountEvidenceService,
)

from .common import (
    PARTIAL_RETENTION_CODES,
    PURCHASE_CODE,
    TOTAL_RETENTION_CODE,
    TOTAL_RETENTION_RATE,
    reject_received,
)


class ReceivedPurchaseEvidenceService(IncomingAccountEvidenceService):
    """Read a signed DTE 46 whose buyer retained the VAT of our sale."""

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
        self._check_retentions(amounts, retentions)
        amounts["retentions"] = retentions
        return document

    def _check_retentions(self, amounts, retentions):
        vat_amount = decimal(amounts["vat"])
        if not vat_amount:
            expected = []
        else:
            expected = [TOTAL_RETENTION_CODE]
        if [item["code"] for item in retentions] != expected or any(
            decimal(item["rate"]) != TOTAL_RETENTION_RATE
            or decimal(item["amount"]) != vat_amount
            for item in retentions
        ):
            reject_received(self.env, "retention_unsupported")
        receivable = (
            decimal(amounts["net"])
            + decimal(amounts["exempt"])
            + vat_amount
            - sum(decimal(item["amount"]) for item in retentions)
        )
        if receivable != decimal(amounts["total"]):
            reject_received(
                self.env,
                "amount_mismatch",
                source_value=amounts["total"],
                odoo_value=receivable,
            )

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
        if any(code in PARTIAL_RETENTION_CODES for code in codes):
            reject_received(self.env, "retention_partial")
        if any(code != TOTAL_RETENTION_CODE for code in codes):
            reject_received(self.env, "retention_unsupported")
        return super()._check_supported_features(document)

    def _additional_code(self, node):
        # Code 15 on a detail marks it as retained; totals carry the amount.
        return None

    def _additional_taxes(self, totals):
        # The retained VAT is not charged on top; extract() reads it.
        return []
