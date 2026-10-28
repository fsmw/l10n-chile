# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import base64
import binascii
import hashlib
from calendar import monthrange
from copy import deepcopy
from datetime import date

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from lxml import etree

from odoo import _, api, models


class RcvMatcher(models.AbstractModel):
    _name = "l10n.cl.rcv.matcher"
    _description = "SII RCV and account.move matcher"

    @api.model
    def _normalize_vat(self, value):
        return self.env["l10n.cl.rcv.normalizer"]._vat(value)

    @api.model
    def _vat_variants(self, value):
        canonical = self._normalize_vat(value)
        rut, separator, dv = canonical.partition("-")
        if not rut or not separator or not dv or not rut.isdigit():
            return {canonical} if canonical else set()
        dotted = "{}-{}".format(format(int(rut), ",").replace(",", "."), dv)
        values = {canonical, dotted}
        for base in (canonical, dotted):
            values.update({"CL" + base, "CL " + base})
        compact = rut + dv
        dotted_compact = dotted.replace("-", "")
        values.update(
            {
                compact,
                dotted_compact,
                "CL" + compact,
                "CL " + compact,
                "CL" + dotted_compact,
                "CL " + dotted_compact,
            }
        )
        return values

    @api.model
    def _partners_by_vat(self, value):
        """Find partners whose RUT is canonically equivalent to value."""
        normalized_vat = self._normalize_vat(value)
        if not normalized_vat:
            return self.env["res.partner"]
        partners = self.env["res.partner"].search(
            [("vat", "in", list(self._vat_variants(normalized_vat)))]
        )
        partners = partners.filtered(
            lambda partner: self._normalize_vat(partner.vat) == normalized_vat
        )
        companies = partners.filtered("is_company")
        return companies or partners

    @api.model
    def _is_generic_export(self, document):
        """Limit the recipient exception to export invoices with the SII placeholder."""
        return (
            document.operation == "sale"
            and document.document_type_code == "110"
            and self._normalize_vat(document.counterpart_vat) == "55555555-5"
        )

    @api.model
    def _export_identity_error(self, document):
        issuer = self._normalize_vat(document.issuer_vat)
        if not issuer or issuer != self._normalize_vat(document.company_id.vat):
            return _("El emisor de la exportación RCV no coincide con la compañía.")
        folio = (document.folio or "").strip()
        if not folio.isascii() or not folio.isdigit() or not int(folio):
            return _("El folio de la exportación RCV no es un entero positivo válido.")
        return False

    @api.model
    def _export_candidate_moves(self, document):
        if self._export_identity_error(document):
            return self.env["account.move"]
        moves = (
            self.env["account.move"]
            .with_company(document.company_id)
            .search(
                [
                    ("company_id", "=", document.company_id.id),
                    ("move_type", "=", "out_invoice"),
                    ("l10n_latam_document_type_id.code", "=", "110"),
                    ("state", "!=", "cancel"),
                ]
            )
        )
        folio = document.folio.strip().lstrip("0")
        return moves.filtered(
            lambda move: (
                (move.l10n_latam_document_number or "").strip().lstrip("0") == folio
            )
        )

    @api.model
    def _candidate_moves(self, document):
        if self._is_generic_export(document):
            return self._export_candidate_moves(document)
        if not document.folio or not document.document_type_code:
            return self.env["account.move"]
        move_types = (
            ["in_invoice", "in_refund", "in_receipt"]
            if document.operation == "purchase"
            else ["out_invoice", "out_refund", "out_receipt"]
        )
        expected_vat = (
            document.issuer_vat
            if document.operation == "purchase"
            else document.counterpart_vat
        )
        normalized_vat = self._normalize_vat(expected_vat)
        partners = self._partners_by_vat(normalized_vat)
        if not partners:
            return self.env["account.move"]
        folio = document.folio.lstrip("0") or "0"
        moves = (
            self.env["account.move"]
            .with_company(document.company_id)
            .search(
                [
                    ("company_id", "=", document.company_id.id),
                    ("move_type", "in", move_types),
                    ("partner_id", "in", partners.ids),
                    (
                        "l10n_latam_document_type_id.code",
                        "=",
                        document.document_type_code,
                    ),
                    ("state", "!=", "cancel"),
                ]
            )
        )
        return moves.filtered(
            lambda move: (
                ((move.l10n_latam_document_number or "").lstrip("0") or "0") == folio
                and self._normalize_vat(move.partner_id.vat)
                == self._normalize_vat(expected_vat)
            )
        )

    @api.model
    def _reconcile(self, documents):
        for document in documents:
            fiscal_evidence = self.env["l10n.cl.rcv.fiscal.evidence"]
            rcv_amounts = fiscal_evidence.rcv_amounts(document)
            document.write(
                {
                    "fiscal_vat_amount": (
                        rcv_amounts["fiscal_vat"] if rcv_amounts["complete"] else False
                    ),
                    "tax_comparison_state": "not_comparable",
                    "tax_allocation_state": "not_comparable",
                    "tax_allocation_note": False,
                    "reconciliation_reason_code": False,
                    "automation_error": False,
                }
            )
            generic_export = self._is_generic_export(document)
            identity_error = (
                self._export_identity_error(document) if generic_export else False
            )
            if identity_error:
                document.write(
                    {
                        "reconciliation_state": "review",
                        "automation_error": identity_error,
                        "reconciliation_reason_code": "invalid_fiscal_identity",
                    }
                )
                self._log_reconciliation_decision(
                    document, self.env["account.move"], "invalid_fiscal_identity", {}
                )
                continue
            candidates = self._candidate_moves(document)
            if len(candidates) > 1:
                document.write(
                    {
                        "reconciliation_state": "duplicate",
                        "automation_error": False,
                        "reconciliation_reason_code": "duplicate_fiscal_identity",
                    }
                )
                self._log_reconciliation_decision(
                    document, self.env["account.move"], "duplicate_fiscal_identity", {}
                )
                continue
            if not candidates:
                document.write(
                    {
                        "reconciliation_state": "missing",
                        "move_id": False,
                        "automation_error": False,
                        "reconciliation_reason_code": "odoo_document_missing",
                    }
                )
                self._log_reconciliation_decision(
                    document, self.env["account.move"], "odoo_document_missing", {}
                )
                continue
            move = candidates[0]
            if document.move_id and document.move_id != move:
                document.write(
                    {
                        "reconciliation_state": "review",
                        "automation_error": _(
                            "El documento ya está vinculado a otro movimiento; "
                            "revise el vínculo antes de conciliar."
                        ),
                        "reconciliation_reason_code": "conflicting_existing_link",
                    }
                )
                self._log_reconciliation_decision(
                    document,
                    move,
                    "conflicting_existing_link",
                    {"linked_move_id": document.move_id.id},
                )
                continue
            document.move_id = move
            if move.state == "draft":
                repair = getattr(
                    self.env["fetchmail.server"],
                    "_l10n_cl_rcv_repair_redundant_summary",
                    None,
                )
                if repair:
                    repair(move)
                move.invalidate_recordset()
            if generic_export:
                self._reconcile_generic_export(document, move)
                self._log_reconciliation_decision(
                    document,
                    move,
                    document.reconciliation_reason_code
                    or document.reconciliation_state,
                    {"generic_export": True},
                )
                continue
            period_error = self._align_draft_accounting_date(document, move)
            move_date = move.date
            period_diff = bool(
                move_date and move_date.strftime("%Y%m") != document.tax_period
            )
            currency = document.currency_id
            move_exempt_amount = self._move_exempt_amount(move)
            zero_tax_exempt = self._has_proven_zero_tax_exempt_case(
                document, move, move_exempt_amount
            )
            if zero_tax_exempt and document.document_type_code == "61":
                # A type 61 reversal without explicit tax tags has no exempt
                # balance for _move_exempt_amount() to discover. Once its
                # type 34 source proves the exemption, use the posted credit
                # note's untaxed company balance for the RCV comparison.
                move_exempt_amount = abs(move.amount_untaxed_signed)
            # Exempt RCV drafts can legitimately have no tax on their invoice
            # line. In that case Odoo's untaxed total still equals the exempt
            # amount, so classify it as exempt instead of comparing it to RCV
            # net (zero).
            if (
                not zero_tax_exempt
                and currency.is_zero(move_exempt_amount)
                and not currency.is_zero(document.exempt_amount)
                and currency.is_zero(
                    abs(move.amount_untaxed_signed)
                    - document.net_amount
                    - document.exempt_amount
                )
            ):
                move_exempt_amount = document.exempt_amount
            move_net_amount = abs(move.amount_untaxed_signed) - move_exempt_amount
            amount_fields = [
                (document.net_amount, move_net_amount),
                (document.exempt_amount, move_exempt_amount),
                (document.total_amount, abs(move.amount_total_signed)),
            ]
            amount_diff = any(
                not currency.is_zero(rcv_amount - move_amount)
                for rcv_amount, move_amount in amount_fields
            )
            (
                tax_state,
                tax_diff,
                zero_vat_conflict,
                source_tax_confirmed,
                rounding_match,
                source_evidence,
            ) = self._compare_reconciliation_tax(
                document,
                move,
                move_net_amount,
                move_exempt_amount,
                amount_diff,
                zero_tax_exempt,
            )
            if rounding_match:
                amount_diff = False
            document.tax_comparison_state = tax_state
            allocation_state, allocation_note = self._compare_tax_allocation(
                document, move
            )
            document.write(
                {
                    "tax_allocation_state": allocation_state,
                    "tax_allocation_note": allocation_note or False,
                }
            )
            amount_diff = amount_diff or tax_diff
            self._set_reconciliation_result(
                document,
                period_error,
                amount_diff,
                period_diff,
                tax_state,
                zero_vat_conflict,
                source_tax_confirmed,
                source_evidence,
            )
            if document.reconciliation_state == "matched":
                if allocation_state == "different":
                    document.reconciliation_reason_code = "tax_allocation_difference"
                elif allocation_state == "not_comparable":
                    document.reconciliation_reason_code = "tax_allocation_unclassified"
            self._log_reconciliation_decision(
                document,
                move,
                document.reconciliation_reason_code,
                {
                    "rcv": rcv_amounts,
                    "odoo_fiscal_vat": fiscal_evidence.odoo_fiscal_vat(move),
                    "amount_diff": amount_diff,
                    "period_diff": period_diff,
                    "tax_comparison_state": tax_state,
                    "tax_allocation_state": allocation_state,
                    "source_evidence": source_evidence,
                },
            )

    @api.model
    def _log_reconciliation_decision(self, document, move, reason, evidence):
        """Persist one idempotent automatic decision for the selected document."""
        self.env["l10n.cl.rcv.match.log"]._log_automatic_decision(
            document,
            move,
            {
                "state": document.reconciliation_state,
                "reason_code": reason,
                "tax_comparison_state": document.tax_comparison_state,
                "tax_allocation_state": document.tax_allocation_state,
            },
            evidence,
        )

    @api.model
    def _set_reconciliation_result(
        self,
        document,
        period_error,
        amount_diff,
        period_diff,
        tax_state,
        zero_vat_conflict,
        source_tax_confirmed,
        source_evidence=None,
    ):
        document.automation_error = period_error or False
        document.reconciliation_state = "pending"
        unresolved_zero_vat = zero_vat_conflict and not source_tax_confirmed
        reason_code = "fiscal_match"
        if period_error or unresolved_zero_vat:
            document.reconciliation_state = "review"
            reason_code = (
                "accounting_period_blocked"
                if period_error
                else (source_evidence or {}).get(
                    "reason_code", "unexplained_tax_residual"
                )
            )
        elif period_diff:
            document.reconciliation_state = "period_diff"
            reason_code = "accounting_period_mismatch"
        elif tax_state == "not_comparable":
            document.reconciliation_state = "review"
            reason_code = "tax_mapping_unavailable"
        elif amount_diff:
            document.reconciliation_state = "amount_diff"
            reason_code = "fiscal_amount_mismatch"
        elif source_tax_confirmed:
            document.reconciliation_state = "matched"
            reason_code = "rcv_source_inconsistency_resolved"
        else:
            document.reconciliation_state = "matched"
        document.reconciliation_reason_code = reason_code
        if document.reconciliation_state != "review" or document.automation_error:
            return
        document.automation_error = (
            _(
                "El RCV declara IVA fiscal cero, pero queda un residual de "
                "%(residual)s. Evidencia XML: %(evidence)s. Se conservó el vínculo.",
                residual=(source_evidence or {}).get("residual", "—"),
                evidence=(source_evidence or {}).get(
                    "message", "falta XML DTE independiente y verificable"
                ),
            )
            if unresolved_zero_vat
            else _(
                "No se pudo comparar el desglose de impuestos RCV con "
                "impuestos contabilizados identificables."
            )
            if tax_state == "not_comparable"
            else _("La evidencia contable o tributaria no permite conciliar.")
        )

    @api.model
    def _matches_line_rounded_dte(
        self, document, move, move_net_amount, move_exempt_amount
    ):
        """Prove a DTE 33 VAT difference caused only by line-level rounding."""
        currency = document.currency_id
        fiscal_evidence = self.env["l10n.cl.rcv.fiscal.evidence"]
        rcv_vat = fiscal_evidence.rcv_fiscal_vat(document)
        booked_vat = fiscal_evidence.odoo_fiscal_vat(move)
        if (
            document.source != "sii"
            or document.operation != "purchase"
            or document.document_type_code != "33"
            or move.move_type != "in_invoice"
            or move.currency_id != currency
            or currency.name != "CLP"
            or move.company_id.tax_calculation_rounding_method != "round_globally"
            or not document.tax_breakdown_available
            or not move._l10n_cl_rcv_dte_attachment("l10n_cl_dte_file")
            or rcv_vat is None
            or booked_vat is None
            or not currency.is_zero(move_net_amount - document.net_amount)
            or not currency.is_zero(move_exempt_amount - document.exempt_amount)
        ):
            return False

        xml_values = self._read_purchase_dte_rounding_values(move)
        if not xml_values or not self._dte_rounding_header_matches(
            document, move, xml_values
        ):
            return False

        details = xml_values["details"]
        invoice_lines = move.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        ).sorted(key=lambda line: (line.sequence, line.id))
        if len(details) != len(invoice_lines) or not details:
            return False

        line_rounded_vat = 0.0
        for detail_amount, line in zip(details, invoice_lines, strict=True):
            line_base = currency.round(
                line.price_unit * line.quantity * (1 - line.discount / 100)
            )
            if not currency.is_zero(line_base - detail_amount):
                return False
            taxes = line.tax_ids.flatten_taxes_hierarchy()
            if len(taxes) != 1:
                return False
            tax = taxes[0]
            if (
                tax.type_tax_use != "purchase"
                or tax.amount_type != "percent"
                or tax.amount <= 0
                or tax.l10n_cl_sii_code != 14
                or not currency.is_zero(tax.amount - xml_values["vat_rate"])
            ):
                return False
            tax_result = tax.compute_all(
                line.price_unit * (1 - line.discount / 100),
                currency=currency,
                quantity=line.quantity,
                product=line.product_id,
                partner=move.partner_id,
            )
            line_rounded_vat += sum(item["amount"] for item in tax_result["taxes"])

        if not currency.is_zero(line_rounded_vat - rcv_vat):
            return False
        vat_difference = rcv_vat - booked_vat
        total_difference = document.total_amount - abs(move.amount_total_signed)
        return not currency.is_zero(vat_difference) and currency.is_zero(
            total_difference - vat_difference
        )

    @api.model
    def _read_purchase_dte_rounding_values(self, move):
        """Read only the identity, totals and simple taxable detail evidence."""
        try:
            attachment = move._l10n_cl_rcv_dte_attachment("l10n_cl_dte_file")
            if not attachment:
                return False
            parser = etree.XMLParser(
                resolve_entities=False, no_network=True, load_dtd=False
            )
            root = etree.fromstring(attachment.raw, parser=parser)
        except (etree.XMLSyntaxError, TypeError, ValueError):
            return False
        documents = root.xpath('.//*[local-name()="Documento"]')
        if root.tag.endswith("Documento"):
            documents = [root]
        if len(documents) != 1:
            return False
        dte_document = documents[0]

        def value(name, parent=dte_document):
            nodes = parent.xpath(".//*[local-name()=$name]", name=name)
            return (nodes[0].text or "").strip() if nodes else ""

        try:
            details = dte_document.xpath('.//*[local-name()="Detalle"]')
            if dte_document.xpath(
                './/*[local-name()="DscRcgGlobal" or local-name()="ImptoReten"]'
            ):
                return False
            if any(detail.xpath('.//*[local-name()="IndExe"]') for detail in details):
                return False
            detail_amounts = [
                float(detail.xpath('.//*[local-name()="MontoItem"]')[0].text or "")
                for detail in details
                if detail.xpath('.//*[local-name()="MontoItem"]')
            ]
            if len(detail_amounts) != len(details):
                return False
            return {
                "document_type": value("TipoDTE"),
                "folio": value("Folio"),
                "issuer_vat": value("RUTEmisor"),
                "document_date": value("FchEmis"),
                "net": float(value("MntNeto") or 0),
                "exempt": float(value("MntExe") or 0),
                "vat": float(value("IVA") or 0),
                "total": float(value("MntTotal") or 0),
                "vat_rate": float(value("TasaIVA") or 0),
                "details": detail_amounts,
            }
        except (IndexError, TypeError, ValueError):
            return False

    @api.model
    def _dte_rounding_header_matches(self, document, move, values):
        currency = document.currency_id
        fiscal_vat = self.env["l10n.cl.rcv.fiscal.evidence"].rcv_fiscal_vat(document)
        if fiscal_vat is None:
            return False
        return (
            values["document_type"] == document.document_type_code
            and (values["folio"].lstrip("0") or "0")
            == (document.folio.lstrip("0") or "0")
            and self._normalize_vat(values["issuer_vat"])
            == self._normalize_vat(document.issuer_vat)
            and values["document_date"] == str(document.document_date)
            and values["document_date"] == str(move.invoice_date)
            and document.document_date
            and move.invoice_date
            and currency.is_zero(values["net"] - document.net_amount)
            and currency.is_zero(values["exempt"] - document.exempt_amount)
            and currency.is_zero(values["vat"] - fiscal_vat)
            and currency.is_zero(values["total"] - document.total_amount)
            and currency.is_zero(sum(values["details"]) - values["net"])
        )

    @api.model
    def _reconcile_generic_export(self, document, move):
        """Compare booked CLP values without revaluing or editing an issued invoice."""
        document.automation_error = False
        document.tax_comparison_state = "not_comparable"
        if move.state != "posted":
            document.write(
                {
                    "reconciliation_state": "review",
                    "automation_error": _(
                        "La exportación debe estar contabilizada "
                        "para conciliar sus saldos."
                    ),
                }
            )
            return
        net = exempt = 0.0
        for line in move.invoice_line_ids.filtered(
            lambda item: item.display_type == "product"
        ):
            taxes = line.tax_ids.flatten_taxes_hierarchy()
            if taxes.filtered(lambda tax: tax.amount_type not in ("percent", "fixed")):
                document.write(
                    {
                        "reconciliation_state": "review",
                        "automation_error": _(
                            "La exportación contiene un impuesto que no se puede "
                            "clasificar como neto o exento."
                        ),
                    }
                )
                return
            if not taxes or all(tax.amount == 0 for tax in taxes):
                exempt -= line.balance
            else:
                net -= line.balance
        currency = document.currency_id
        checks = (
            (document.net_amount, net),
            (document.exempt_amount, exempt),
            (document.total_amount, abs(move.amount_total_signed)),
        )
        tax_state, tax_diff = self._compare_export_tax(document, move)
        period_diff = move.date.strftime("%Y%m") != document.tax_period
        if (
            self._has_proven_zero_tax_exempt_case(document, move, exempt)
            and not period_diff
        ):
            tax_state, tax_diff = "matched", False
        document.tax_comparison_state = tax_state
        amount_diff = tax_diff or any(
            not currency.is_zero(expected - actual) for expected, actual in checks
        )
        if period_diff:
            document.reconciliation_state = "period_diff"
        elif tax_state == "not_comparable":
            document.reconciliation_state = "review"
        elif amount_diff:
            document.reconciliation_state = "amount_diff"
        else:
            document.reconciliation_state = "matched"
        if document.reconciliation_state == "review":
            document.automation_error = (
                _(
                    "No se pudo comparar el desglose de impuestos RCV con "
                    "impuestos contabilizados identificables."
                )
                if tax_state == "not_comparable"
                else _("La evidencia contable o tributaria no permite conciliar.")
            )

    @api.model
    def _has_proven_zero_tax_exempt_case(self, document, move, exempt_amount):
        """Recognize only fully exempt DTEs whose posted CLP balances prove zero VAT."""
        currency = document.currency_id
        if document.document_type_code == "61":
            return self._has_proven_exempt_dte61_reversal(document, move)
        if (
            document.operation != "sale"
            or document.document_type_code not in {"34", "110"}
            or move.state != "posted"
            or not currency.is_zero(document.net_amount)
            or not currency.is_zero(document.vat_amount)
            or not currency.is_zero(document.vat_not_recoverable)
            or not currency.is_zero(document.common_vat)
            or not currency.is_zero(document.exempt_amount - document.total_amount)
            or not currency.is_zero(exempt_amount - document.total_amount)
            or move.date.strftime("%Y%m") != document.tax_period
        ):
            return False
        vat_tag = self.env.ref("l10n_cl.tag_cl_sale_mnt_iva", raise_if_not_found=False)
        vat_lines = move.line_ids.filtered(
            lambda line: (
                line.tax_line_id
                and (
                    line.tax_line_id.amount_type == "percent"
                    and line.tax_line_id.amount != 0
                    or vat_tag
                    and vat_tag in line.tax_tag_ids
                )
            )
        )
        if vat_lines:
            return False
        return True

    @api.model
    def _has_proven_exempt_dte61_reversal(self, document, move):
        """Prove an exempt type 61 against its posted type 34 source invoice."""
        currency = document.currency_id
        source = move.reversed_entry_id
        if (
            document.operation != "sale"
            or move.state != "posted"
            or move.move_type != "out_refund"
            or move.l10n_latam_document_type_id.code != "61"
            or not source
            or source.state != "posted"
            or source.move_type != "out_invoice"
            or source.l10n_latam_document_type_id.code != "34"
            or move.company_id != document.company_id
            or source.company_id != move.company_id
            or move.partner_id != source.partner_id
            or currency.name != "CLP"
            or move.currency_id != currency
            or source.currency_id != currency
            or not currency.is_zero(document.net_amount)
            or not currency.is_zero(document.vat_amount)
            or not currency.is_zero(document.vat_not_recoverable)
            or not currency.is_zero(document.common_vat)
            or not currency.is_zero(document.exempt_amount - document.total_amount)
            or move.date.strftime("%Y%m") != document.tax_period
            or not currency.is_zero(
                abs(move.amount_untaxed_signed) - document.total_amount
            )
            or not currency.is_zero(
                abs(move.amount_total_signed) - document.total_amount
            )
            or not currency.is_zero(
                abs(source.amount_untaxed_signed) - abs(source.amount_total_signed)
            )
            or not currency.is_zero(
                self._move_exempt_amount(source) - abs(source.amount_untaxed_signed)
            )
            or self._move_has_nonzero_taxes(move)
            or self._move_has_nonzero_taxes(source)
        ):
            return False
        return True

    @api.model
    def _move_has_nonzero_taxes(self, move):
        """Return whether invoice tax configuration or booked tax is non-zero."""
        product_lines = move.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        taxes = product_lines.tax_ids.flatten_taxes_hierarchy()
        if any(tax.amount for tax in taxes):
            return True
        tax_lines = move.line_ids.filtered("tax_line_id")
        return any(
            not move.company_currency_id.is_zero(line.balance) for line in tax_lines
        )

    @api.model
    def _compare_export_tax(self, document, move):
        """Require an RCV breakdown and identifiable booked taxes or zero VAT."""
        if not document.tax_breakdown_available:
            return "not_comparable", False
        tax_lines = move.line_ids.filtered("tax_line_id")
        tag = self.env.ref("l10n_cl.tag_cl_sale_mnt_iva", raise_if_not_found=False)
        if tax_lines and (
            not tag or any(tag not in line.tax_tag_ids for line in tax_lines)
        ):
            return "not_comparable", False
        vat = -sum(tax_lines.mapped("balance"))
        different = not document.currency_id.is_zero(document.vat_amount - vat)
        return ("different" if different else "matched"), different

    @api.model
    def _align_draft_accounting_date(self, document, move):
        """Align an eligible draft move with its RCV tax period, preserving DTE date."""
        period = (document.tax_period or "").strip()
        if len(period) != 6 or not period.isdigit():
            return _("El período tributario RCV no tiene formato AAAAMM válido.")
        year, month = int(period[:4]), int(period[4:])
        if year < 1 or month < 1 or month > 12:
            return _("El período tributario RCV no es válido.")
        target_date = date(year, month, monthrange(year, month)[1])
        if move.date and move.date.strftime("%Y%m") == period:
            return False
        if move.state != "draft":
            return _(
                "El movimiento relacionado ya está publicado y su fecha contable "
                "no corresponde al período RCV %(period)s.",
                period=period,
            )

        has_tax = bool(
            move.line_ids.filtered(
                lambda line: line.tax_ids or line.tax_line_id or line.tax_tag_ids
            )
        )
        lock_dates = move._get_violated_lock_dates(target_date, has_tax)
        if lock_dates:
            lock_info = self.env["res.company"]._format_lock_dates(lock_dates)
            return _(
                "No se pudo alinear la fecha contable al período RCV %(period)s "
                "por bloqueos contables: %(locks)s.",
                period=period,
                locks=lock_info,
            )

        previous_date = move.date
        move.write({"date": target_date})
        move.message_post(
            body=_(
                "Fecha contable alineada al período tributario RCV %(period)s "
                "(%(old)s → %(new)s). Se conservó la fecha de emisión del DTE.",
                period=period,
                old=previous_date or "—",
                new=target_date,
            )
        )
        return False

    @api.model
    def _compare_reconciliation_tax(
        self,
        document,
        move,
        move_net_amount,
        move_exempt_amount,
        amount_diff,
        zero_tax_exempt,
    ):
        tax_state, tax_diff = self._compare_tax_components(document, move)
        currency = document.currency_id
        rcv_amounts = self.env["l10n.cl.rcv.fiscal.evidence"].rcv_amounts(document)
        residual = self.env["l10n.cl.rcv.fiscal.evidence"].rcv_unexplained_residual(
            document
        )
        zero_vat_conflict = (
            document.operation == "purchase"
            and document.document_type_code in ("33", "61")
            and rcv_amounts["complete"]
            and currency.is_zero(rcv_amounts["fiscal_vat"])
            and residual is not None
            and not currency.is_zero(residual)
        )
        source_evidence = (
            self._resolve_stored_dte_evidence(
                document, move, move_net_amount, move_exempt_amount, residual
            )
            if zero_vat_conflict
            else {"verified": False, "reason_code": "not_required"}
        )
        source_tax_confirmed = zero_vat_conflict and source_evidence["verified"]
        if source_tax_confirmed:
            tax_state, tax_diff = "source_confirmed", False
            amount_diff = False
        rounding_match = bool(
            not zero_tax_exempt
            and amount_diff
            and tax_diff
            and self._matches_line_rounded_dte(
                document, move, move_net_amount, move_exempt_amount
            )
        )
        if zero_tax_exempt:
            tax_state, tax_diff = "matched", False
        elif rounding_match:
            tax_state, tax_diff = "matched", False
        return (
            tax_state,
            tax_diff,
            zero_vat_conflict,
            source_tax_confirmed,
            rounding_match,
            source_evidence,
        )

    @api.model
    def _compare_tax_components(self, document, move):
        if not document.tax_breakdown_available:
            return "not_comparable", False
        if document.operation == "purchase":
            evidence = self.env["l10n.cl.rcv.fiscal.evidence"]
            rcv_vat = evidence.rcv_fiscal_vat(document)
            odoo_vat = evidence.odoo_fiscal_vat(move)
            if rcv_vat is None or odoo_vat is None:
                return "not_comparable", False
            different = not document.currency_id.is_zero(rcv_vat - odoo_vat)
            return ("different" if different else "matched"), different
        recoverable_xmlid = "l10n_cl.tag_cl_sale_mnt_iva"
        recoverable = self._tagged_tax_amount(move, recoverable_xmlid)
        if recoverable is None:
            return "not_comparable", False
        different = not document.currency_id.is_zero(document.vat_amount - recoverable)
        return ("different" if different else "matched"), different

    @api.model
    def _tagged_purchase_reversal_vat(self, move, normal_amount):
        """Return credit-note VAT by SII identity and reversal direction."""
        return self.env["l10n.cl.rcv.fiscal.evidence"].odoo_fiscal_vat(move)

    @api.model
    def _compare_tax_allocation(self, document, move):
        """Compare RCV credit allocation separately from fiscal reconciliation."""
        if document.operation != "purchase" or not document.tax_breakdown_available:
            return "not_comparable", False
        evidence = self.env["l10n.cl.rcv.fiscal.evidence"]
        components = (
            (
                "IVA recuperable",
                document.vat_amount,
                "l10n_cl.tag_cl_purchase_mnt_iva",
            ),
            (
                "IVA no recuperable",
                document.vat_not_recoverable,
                "l10n_cl.tag_cl_purchase_mnt_iva_no_rec",
            ),
            (
                "IVA de uso común",
                document.common_vat,
                "l10n_cl.tag_cl_purchase_mnt_iva_uso_comun",
            ),
        )
        comparisons = [
            (label, rcv_amount, evidence.tax_component_amount(move, tag_xmlid))
            for label, rcv_amount, tag_xmlid in components
        ]
        if any(odoo_amount is None for _, _, odoo_amount in comparisons):
            return "not_comparable", _(
                "El IVA fiscal coincide, pero no se pudo identificar de forma "
                "única su clasificación contable por componente."
            )
        differences = [
            (label, rcv_amount, odoo_amount)
            for label, rcv_amount, odoo_amount in comparisons
            if not document.currency_id.is_zero(rcv_amount - odoo_amount)
        ]
        if not differences:
            return "matched", False
        note = _(
            "El documento fiscal está conciliado; solo difiere la clasificación "
            "del IVA: %(details)s.",
            details="; ".join(
                _(
                    "%(component)s: RCV %(rcv)s / Odoo %(odoo)s",
                    component=label,
                    rcv=rcv_amount,
                    odoo=odoo_amount,
                )
                for label, rcv_amount, odoo_amount in differences
            ),
        )
        return "different", note

    @api.model
    def _zero_vat_source_confirms(
        self, document, move, move_net_amount, move_exempt_amount
    ):
        """Compatibility wrapper around the structured signed-evidence resolver."""
        residual = self.env["l10n.cl.rcv.fiscal.evidence"].rcv_unexplained_residual(
            document
        )
        return self._resolve_stored_dte_evidence(
            document, move, move_net_amount, move_exempt_amount, residual
        )["verified"]

    @api.model
    def _stored_dte_attachments(self, move, limit=50):
        """Collect a bounded set of XML candidates related to this move only."""
        attachments = self.env["ir.attachment"]
        for field_name in ("l10n_cl_dte_file", "l10n_cl_dte_source_file"):
            if field_name in move._fields and move[field_name]:
                attachments |= move[field_name]
        # Without the optional DTE localization the XML is linked only by
        # res_model/res_id, so it is not reachable through the DTE fields.
        attachments |= self.env["ir.attachment"].search(
            [
                ("res_model", "=", move._name),
                ("res_id", "=", move.id),
                ("name", "ilike", "%.xml"),
            ],
            limit=limit + 1,
            order="id desc",
        )
        if (
            "message_main_attachment_id" in move._fields
            and move.message_main_attachment_id
        ):
            attachments |= move.message_main_attachment_id
        if move.message_ids:
            attachments |= move.message_ids.mapped("attachment_ids")
        if "attachment_ids" in move._fields:
            attachments |= move.attachment_ids
        attachments = attachments.sorted("id")
        xml_candidates = attachments.filtered(
            lambda attachment: (
                (attachment.mimetype or "").lower()
                in ("application/xml", "text/xml", "application/x-xml")
                or (attachment.name or "").lower().endswith((".xml", ".xml.gz"))
            )
        )
        return xml_candidates[: limit + 1], len(xml_candidates) > limit

    @api.model
    def _resolve_stored_dte_evidence(  # noqa: C901
        self, document, move, move_net_amount, move_exempt_amount, residual
    ):
        """Verify only company-scoped stored DTE candidates with exact identity.

        Candidate names and MIME types only narrow the attachment search; fiscal
        identity, signature, digest and amounts determine whether evidence is
        accepted. The search is limited to the selected move and its chatter.
        """
        base_result = {
            "verified": False,
            "reason_code": "missing_xml",
            "message": "No se encontró un XML DTE relacionado con el documento.",
            "residual": residual,
            "digests": [],
        }
        if move.move_type not in ("in_invoice", "in_refund"):
            return base_result
        attachments, truncated = self._stored_dte_attachments(move)
        if not attachments:
            return base_result

        def value(parent, name):
            nodes = parent.xpath(".//*[local-name()=$name]", name=name)
            return (nodes[0].text or "").strip() if nodes else ""

        expected_folio = (document.folio or "").lstrip("0") or "0"
        expected_recipient = (
            document.counterpart_vat or document.company_id.partner_id.vat
        )
        currency = document.currency_id
        fiscal_evidence = self.env["l10n.cl.rcv.fiscal.evidence"]
        booked_vat = fiscal_evidence.odoo_fiscal_vat(move)
        matching_identity_seen = False
        invalid_xml_seen = False
        identity_mismatch_seen = False
        failures = []
        verified = {}

        for attachment in attachments[:50]:
            try:
                source_bytes = attachment.raw
                parser = etree.XMLParser(
                    resolve_entities=False, no_network=True, load_dtd=False
                )
                root = etree.fromstring(source_bytes, parser=parser)
            except (etree.XMLSyntaxError, TypeError, ValueError):
                invalid_xml_seen = True
                continue
            dtes = root.xpath('.//*[local-name()="DTE"]')
            if etree.QName(root).localname == "DTE":
                dtes = [root]
            if not dtes and etree.QName(root).localname == "Documento":
                dtes = [root]

            for dte in dtes:
                dte_documents = dte.xpath('.//*[local-name()="Documento"]')
                if etree.QName(dte).localname == "Documento":
                    dte_documents = [dte]
                for dte_doc in dte_documents:
                    try:
                        identity_matches = (
                            value(dte_doc, "TipoDTE")
                            == str(document.document_type_code)
                            and (value(dte_doc, "Folio").lstrip("0") or "0")
                            == expected_folio
                            and self._normalize_vat(value(dte_doc, "RUTEmisor"))
                            == self._normalize_vat(document.issuer_vat)
                            and self._normalize_vat(value(dte_doc, "RUTRecep"))
                            == self._normalize_vat(expected_recipient)
                        )
                    except (TypeError, ValueError):
                        identity_matches = False
                    if not identity_matches:
                        identity_mismatch_seen = True
                        continue
                    matching_identity_seen = True
                    document_id = dte_doc.get("ID") or False
                    verification = self._verify_dte_xml_signature_diagnostic(
                        dte,
                        source_content=source_bytes,
                        source_document_id=document_id,
                    )
                    if not verification.get("verified"):
                        failures.append(verification)
                        continue

                    totals = dte_doc.xpath('.//*[local-name()="Totales"]')
                    if len(totals) != 1:
                        failures.append(
                            {
                                "reason_code": "amount_mismatch",
                                "message": (
                                    "El DTE firmado no contiene un único bloque "
                                    "de totales."
                                ),
                            }
                        )
                        continue
                    try:
                        net, exempt, vat, total = (
                            int(value(totals[0], name) or 0)
                            for name in ("MntNeto", "MntExe", "IVA", "MntTotal")
                        )
                    except (TypeError, ValueError):
                        failures.append(
                            {
                                "reason_code": "amount_mismatch",
                                "message": "El DTE firmado contiene totales inválidos.",
                            }
                        )
                        continue
                    totals_match = (
                        booked_vat is not None
                        and residual is not None
                        and currency.is_zero(document.net_amount - net)
                        and currency.is_zero(document.exempt_amount - exempt)
                        and currency.is_zero(document.total_amount - total)
                        and currency.is_zero(move_net_amount - net)
                        and currency.is_zero(move_exempt_amount - exempt)
                        and currency.is_zero(booked_vat - vat)
                        and currency.is_zero(residual - vat)
                        and currency.is_zero(total - net - exempt - vat)
                    )
                    if not totals_match:
                        failures.append(
                            {
                                "reason_code": "amount_mismatch",
                                "message": (
                                    "Los montos neto, exento, IVA o total del XML "
                                    "firmado no coinciden con RCV y Odoo."
                                ),
                            }
                        )
                        continue
                    digest = hashlib.sha256(source_bytes).hexdigest()
                    verified[digest] = {
                        "digest": digest,
                        "document_id": document_id,
                    }

        if truncated:
            return {
                **base_result,
                "reason_code": "ambiguous_source",
                "message": (
                    "La búsqueda de XML superó el límite de adjuntos; "
                    "la evidencia queda incompleta."
                ),
            }
        if len(verified) > 1:
            return {
                **base_result,
                "reason_code": "ambiguous_source",
                "message": (
                    "Hay varios XML DTE firmados distintos para la misma identidad; "
                    "no se eligió uno."
                ),
                "digests": sorted(verified),
            }
        if len(verified) == 1:
            digest = next(iter(verified))
            return {
                "verified": True,
                "reason_code": "verified",
                "message": "La firma, identidad y montos del XML coinciden.",
                "residual": residual,
                "digests": [digest],
                **verified[digest],
            }

        result = failures[0] if failures else {}
        reason = result.get("reason_code")
        if reason:
            return {
                **base_result,
                "reason_code": reason,
                "message": result.get("message") or "No se pudo verificar el XML DTE.",
            }
        if matching_identity_seen:
            return {
                **base_result,
                "reason_code": (
                    "invalid_xml" if invalid_xml_seen else "invalid_signature"
                ),
                "message": "El XML DTE relacionado no pudo verificarse.",
            }
        if identity_mismatch_seen:
            return {
                **base_result,
                "reason_code": "identity_mismatch",
                "message": (
                    "Los XML almacenados no corresponden a la identidad fiscal RCV."
                ),
            }
        return {
            **base_result,
            "reason_code": "invalid_xml" if invalid_xml_seen else "missing_xml",
            "message": (
                "Los adjuntos relacionados no contienen un XML DTE legible y "
                "coincidente."
            ),
        }

    @api.model
    def _verify_dte_xml_signature(self, dte):
        """Compatibility wrapper for callers that only need a verified flag."""
        return self._verify_dte_xml_signature_diagnostic(dte)["verified"]

    @api.model
    def _verify_dte_xml_signature_diagnostic(  # noqa: C901
        self, dte, source_content=None, source_document_id=None
    ):
        """Verify XMLDSig and return a stable reason code for each failure."""

        def result(reason_code, stage, message):
            return {
                "verified": reason_code == "verified",
                "reason_code": reason_code,
                "stage": stage,
                "message": message,
            }

        if source_content:
            try:
                parser = etree.XMLParser(
                    resolve_entities=False, no_network=True, load_dtd=False
                )
                source_bytes = (
                    source_content.encode()
                    if isinstance(source_content, str)
                    else source_content
                )
                source_root = etree.fromstring(source_bytes, parser=parser)
            except (etree.XMLSyntaxError, TypeError, ValueError):
                return result(
                    "invalid_xml", "parse", "No fue posible leer el XML original."
                )
            dtes = source_root.xpath('.//*[local-name()="DTE"]')
            if etree.QName(source_root).localname == "DTE":
                dtes = [source_root]
            if source_document_id:
                dtes = [
                    candidate
                    for candidate in dtes
                    if candidate.xpath(
                        './/*[local-name()="Documento"][@ID=$identifier]',
                        identifier=source_document_id,
                    )
                ]
            if len(dtes) != 1:
                return result(
                    "identity_mismatch",
                    "locator",
                    "El localizador no identifica un único DTE en el XML original.",
                )
            dte = dtes[0]

        signatures = dte.xpath('.//*[local-name()="Signature"]')
        if len(signatures) != 1:
            return result(
                "invalid_signature", "structure", "El DTE no contiene una firma única."
            )
        signature = signatures[0]
        signed_info_nodes = signature.xpath('./*[local-name()="SignedInfo"]')
        signature_values = signature.xpath('./*[local-name()="SignatureValue"][text()]')
        if len(signed_info_nodes) != 1 or len(signature_values) != 1:
            return result("invalid_signature", "structure", "La firma está incompleta.")
        signed_info = signed_info_nodes[0]
        canonicalization = signed_info.xpath(
            './*[local-name()="CanonicalizationMethod"]/@Algorithm'
        )
        signature_algorithm = signed_info.xpath(
            './*[local-name()="SignatureMethod"]/@Algorithm'
        )
        references = signed_info.xpath('./*[local-name()="Reference"]')
        key_values = signature.xpath('.//*[local-name()="RSAKeyValue"]')
        if (
            len(canonicalization) != 1
            or canonicalization[0]
            not in (
                "http://www.w3.org/TR/2001/REC-xml-c14n-20010315",
                "http://www.w3.org/2001/10/xml-exc-c14n#",
            )
            or len(signature_algorithm) != 1
            or signature_algorithm[0]
            not in (
                "http://www.w3.org/2000/09/xmldsig#rsa-sha1",
                "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
            )
            or len(references) != 1
            or len(key_values) != 1
        ):
            return result(
                "unsupported_signature",
                "algorithm",
                "La firma usa algoritmos o estructura que no se pueden verificar.",
            )
        reference = references[0]
        uri = reference.get("URI", "")
        if not uri.startswith("#") or len(uri) < 2:
            return result(
                "invalid_signature",
                "reference",
                "La referencia firmada no identifica un Documento.",
            )
        referenced = dte.xpath("//*[@ID=$identifier]", identifier=uri[1:])
        if len(referenced) != 1 or etree.QName(referenced[0]).localname != "Documento":
            return result(
                "identity_mismatch",
                "reference",
                "La firma no apunta a un único Documento del DTE.",
            )
        transforms = reference.xpath('./*[local-name()="Transforms"]/*')
        c14n_algorithms = {
            "http://www.w3.org/TR/2001/REC-xml-c14n-20010315": False,
            "http://www.w3.org/2001/10/xml-exc-c14n#": True,
        }
        reference_c14n = [
            item.get("Algorithm")
            for item in transforms
            if item.get("Algorithm") in c14n_algorithms
        ]
        if (
            any(
                item.get("Algorithm")
                not in (
                    *c14n_algorithms,
                    "http://www.w3.org/2000/09/xmldsig#enveloped-signature",
                )
                for item in transforms
            )
            or len(reference_c14n) > 1
        ):
            return result(
                "unsupported_signature",
                "reference_transforms",
                "La referencia usa transforms XMLDSig no soportados.",
            )
        # CanonicalizationMethod applies to SignedInfo only. Reference node sets
        # use their own transform; without one XMLDSig's default is inclusive C14N.
        reference_exclusive = (
            c14n_algorithms[reference_c14n[0]] if reference_c14n else False
        )
        target = deepcopy(referenced[0])
        if any(
            item.get("Algorithm")
            == "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
            for item in transforms
        ):
            for nested_signature in target.xpath('.//*[local-name()="Signature"]'):
                nested_signature.getparent().remove(nested_signature)
        digest_algorithms = reference.xpath(
            './*[local-name()="DigestMethod"]/@Algorithm'
        )
        digest_values = reference.xpath('./*[local-name()="DigestValue"][text()]')
        if len(digest_algorithms) != 1 or len(digest_values) != 1:
            return result(
                "invalid_signature",
                "digest",
                "La referencia no contiene un digest completo.",
            )
        digest_type = {
            "http://www.w3.org/2000/09/xmldsig#sha1": hashlib.sha1,
            "http://www.w3.org/2001/04/xmlenc#sha256": hashlib.sha256,
        }.get(digest_algorithms[0])
        if not digest_type:
            return result(
                "unsupported_signature",
                "digest",
                "El algoritmo de digest no está soportado.",
            )
        target_bytes = etree.tostring(
            target, method="c14n", exclusive=reference_exclusive
        )
        if (
            base64.b64encode(digest_type(target_bytes).digest()).decode()
            != (digest_values[0].text or "").strip()
        ):
            return result(
                "invalid_signature", "digest", "El digest del Documento no coincide."
            )
        rsa_values = key_values[0]
        modulus = rsa_values.xpath('string(./*[local-name()="Modulus"])')
        exponent = rsa_values.xpath('string(./*[local-name()="Exponent"])')
        try:
            public_key = rsa.RSAPublicNumbers(
                int.from_bytes(base64.b64decode(exponent), "big"),
                int.from_bytes(base64.b64decode(modulus), "big"),
            ).public_key()
            signed_bytes = etree.tostring(
                signed_info,
                method="c14n",
                exclusive=canonicalization[0].endswith("xml-exc-c14n#"),
            )
            signature_bytes = base64.b64decode(
                (signature_values[0].text or "").strip(), validate=True
            )
            algorithm = (
                hashes.SHA1()
                if signature_algorithm[0].endswith("rsa-sha1")
                else hashes.SHA256()
            )
            public_key.verify(
                signature_bytes, signed_bytes, padding.PKCS1v15(), algorithm
            )
        except (ValueError, TypeError, binascii.Error, InvalidSignature):
            return result(
                "invalid_signature",
                "signed_info",
                "La firma criptográfica no es válida.",
            )
        return result("verified", "complete", "El digest y la firma son válidos.")

    @api.model
    def _tagged_tax_amount(self, move, xmlid):
        tag = self.env.ref(xmlid, raise_if_not_found=False)
        if not tag:
            return None
        lines = move.line_ids.filtered(
            lambda line: line.tax_line_id and tag in line.tax_tag_ids
        )
        return sum(abs(line.balance) for line in lines)

    @api.model
    def _reconcile_moves(self, sync):
        """Mark Odoo moves missing from SII only for fully covered sections."""
        coverage_model = self.env["l10n.cl.rcv.section"]
        documents = self.env["l10n.cl.rcv.document"].search(
            [
                ("company_id", "=", sync.company_id.id),
                ("operation", "=", sync.operation),
                ("source", "=", "sii"),
            ]
        )
        known = {
            (
                doc.document_type_code,
                self._normalize_vat(
                    doc.issuer_vat
                    if sync.operation == "purchase"
                    else doc.counterpart_vat
                ),
                doc.folio.lstrip("0") or "0",
            )
            for doc in documents
        }
        export_folios = {
            doc.folio.strip().lstrip("0")
            for doc in documents
            if self._is_generic_export(doc) and not self._export_identity_error(doc)
        }
        period_start = f"{sync.period_id.year:04d}-{sync.period_id.month:02d}-01"
        if sync.period_id.month == 12:
            next_start = f"{sync.period_id.year + 1:04d}-01-01"
        else:
            next_start = f"{sync.period_id.year:04d}-{sync.period_id.month + 1:02d}-01"
        move_types = (
            ("in_invoice", "in_refund")
            if sync.operation == "purchase"
            else ("out_invoice", "out_refund")
        )
        moves = self.env["account.move"].search(
            [
                ("company_id", "=", sync.company_id.id),
                ("move_type", "in", move_types),
                ("state", "!=", "cancel"),
                "|",
                "&",
                ("invoice_date", ">=", period_start),
                ("invoice_date", "<", next_start),
                "&",
                ("date", ">=", period_start),
                ("date", "<", next_start),
            ]
        )
        for move in moves:
            document_type = move.l10n_latam_document_type_id
            code = str(document_type.code or "") if document_type else ""
            # Do not infer anything for boletas or unsupported document types.
            if not code or code in {"39", "41"}:
                continue
            required_states = (
                {"REGISTRO", "RECLAMADO", "PENDIENTE"}
                if sync.operation == "purchase"
                else {"REGISTRO"}
            )
            section = coverage_model.search(
                [
                    ("sync_id", "=", sync.id),
                    ("sii_state", "in", list(required_states)),
                ]
            )
            fully_covered = {
                item.sii_state for item in section
            } == required_states and all(item.state == "complete" for item in section)
            partner_vat = self._normalize_vat(move.partner_id.vat)
            folio = (move.l10n_latam_document_number or "").lstrip("0") or "0"
            present = (code, partner_vat, folio) in known
            if (
                sync.operation == "sale"
                and code == "110"
                and move.move_type == "out_invoice"
            ):
                present = present or folio in export_folios
            move.write(
                {
                    "l10n_cl_rcv_reconciliation_state": (
                        "matched"
                        if present
                        else "not_in_sii"
                        if fully_covered
                        else "indeterminate"
                    ),
                    "l10n_cl_rcv_sync_id": sync.id,
                }
            )

    @api.model
    def _move_exempt_amount(self, move):
        if (
            move.move_type == "out_invoice"
            and move.l10n_latam_document_type_id.code == "34"
        ):
            exempt_lines = move.invoice_line_ids.filtered(
                lambda line: (
                    line.display_type == "product"
                    and (
                        not line.tax_ids or all(tax.amount == 0 for tax in line.tax_ids)
                    )
                )
            )
            return sum(abs(line.balance) for line in exempt_lines)
        exempt_tax_ids = move.line_ids.tax_ids.filtered(lambda tax: tax.amount == 0)
        exempt_lines = move.invoice_line_ids.filtered(
            lambda line: bool(line.tax_ids & exempt_tax_ids)
        )
        return sum(abs(line.balance) for line in exempt_lines)
