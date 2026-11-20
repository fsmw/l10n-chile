# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Find the journal entries that carry the fiscal identity of an RCV document."""

from .rcv_values import normalize_vat

PURCHASE_MOVE_TYPES = ("in_invoice", "in_refund", "in_receipt")
SALE_MOVE_TYPES = ("out_invoice", "out_refund", "out_receipt")
EXPORT_DOCUMENT_CODE = "110"
# RUT the SII uses as the recipient of export invoices to foreign customers.
GENERIC_EXPORT_RUT = "55555555-5"
REJECTED_DTE_STATE = "rejected"


def normalize_folio(value):
    """Return a folio without spaces and leading zeros (``"0"`` for zero)."""
    return str(value or "").strip().lstrip("0") or "0"


def vat_variants(value):
    """Return the usual spellings of a RUT, to search partners by VAT."""
    canonical = normalize_vat(value)
    number, separator, digit = canonical.partition("-")
    if not (number.isdigit() and separator and digit):
        return {canonical} if canonical else set()
    dotted = "{}-{}".format(f"{int(number):,}".replace(",", "."), digit)
    compact = number + digit
    bases = {canonical, dotted, compact, dotted.replace("-", "")}
    variants = set(bases)
    for base in bases:
        variants.update({"CL" + base, "CL " + base})
    return variants


def is_generic_export(document):
    """Tell whether ``document`` is an export invoice to the SII placeholder RUT."""
    return (
        document.operation == "sale"
        and document.document_type_code == EXPORT_DOCUMENT_CODE
        and normalize_vat(document.counterpart_vat) == GENERIC_EXPORT_RUT
    )


def partner_vat(document):
    """Return the RUT of the other party of ``document``."""
    if document.operation == "purchase":
        return normalize_vat(document.issuer_vat)
    return normalize_vat(document.counterpart_vat)


class RcvCandidateFinder:
    """Search candidate journal entries by document type, folio and RUT."""

    def __init__(self, env):
        self.env = env

    def export_identity_error(self, document):
        """Return why an export document cannot be identified, or False."""
        env = self.env
        issuer = normalize_vat(document.issuer_vat)
        if not issuer or issuer != normalize_vat(document.company_id.vat):
            return env._("The issuer of the RCV export does not match the company.")
        folio = (document.folio or "").strip()
        if not (folio.isascii() and folio.isdigit() and int(folio)):
            return env._("The folio of the RCV export is not a positive integer.")
        return False

    def candidate_moves(self, document):
        """Return the non-cancelled journal entries matching ``document``."""
        moves = self.env["account.move"]
        if not document.folio or not document.document_type_code:
            return moves
        if is_generic_export(document) and self.export_identity_error(document):
            return moves
        if document.operation == "sale":
            issued = self._issued_dte_moves(document)
            if issued:
                return issued
        if is_generic_export(document):
            return self._export_moves(document)
        return self._partner_moves(document)

    def _base_domain(self, document):
        move_types = (
            PURCHASE_MOVE_TYPES if document.operation == "purchase" else SALE_MOVE_TYPES
        )
        return [
            ("company_id", "=", document.company_id.id),
            ("move_type", "in", move_types),
            ("state", "!=", "cancel"),
        ]

    def _issued_dte_moves(self, document):
        """Return the entries of our issued DTEs with this type and folio.

        A rejected DTE is not a fiscal document and is ignored. Unless the
        document is a generic export, the customer RUT must match too.
        """
        folio = normalize_folio(document.folio)
        if not folio.isdigit():
            return self.env["account.move"]
        dtes = self.env["l10n_cl.electronic.document"].search(
            [
                ("company_id", "=", document.company_id.id),
                ("document_code", "=", document.document_type_code),
                ("folio", "=", int(folio)),
                ("fiscal_state", "!=", REJECTED_DTE_STATE),
                ("source_move_id", "!=", False),
            ]
        )
        domain = self._base_domain(document)
        moves = dtes.source_move_id.filtered_domain(domain)
        if is_generic_export(document):
            return moves
        return moves.filtered(lambda move: self._has_partner_vat(move, document))

    def _export_moves(self, document):
        domain = self._base_domain(document) + [
            ("move_type", "=", "out_invoice"),
            ("l10n_latam_document_type_id.code", "=", EXPORT_DOCUMENT_CODE),
        ]
        return self._with_folio(self.env["account.move"].search(domain), document)

    def _partner_moves(self, document):
        partners = self._commercial_partners(partner_vat(document))
        if not partners:
            return self.env["account.move"]
        domain = self._base_domain(document) + [
            ("commercial_partner_id", "in", partners.ids),
            ("l10n_latam_document_type_id.code", "=", document.document_type_code),
        ]
        moves = self._with_folio(self.env["account.move"].search(domain), document)
        return moves.filtered(lambda move: self._has_partner_vat(move, document))

    def _commercial_partners(self, vat):
        """Return the commercial partners whose RUT is equivalent to ``vat``."""
        if not vat:
            return self.env["res.partner"]
        partners = (
            self.env["res.partner"]
            .with_context(active_test=False)
            .search([("vat", "in", sorted(vat_variants(vat)))])
        )
        partners = partners.filtered(lambda partner: normalize_vat(partner.vat) == vat)
        return partners.commercial_partner_id

    @staticmethod
    def _with_folio(moves, document):
        folio = normalize_folio(document.folio)
        return moves.filtered(
            lambda move: normalize_folio(move.l10n_latam_document_number) == folio
        )

    @staticmethod
    def _has_partner_vat(move, document):
        return normalize_vat(move.commercial_partner_id.vat) == partner_vat(document)
