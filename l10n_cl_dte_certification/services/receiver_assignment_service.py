# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _


class CertificationReceiverAssignmentService:
    """Reuse existing fiscal contacts while respecting the SET reference chain."""

    def assign(self, certification_set):
        certification_set.ensure_one()
        certification_set._check_certification_access()
        certification_set._lock_configuration()
        cases = certification_set.case_ids.sorted("sequence")
        caf_model = certification_set.env["l10n_cl.caf.authorization"]
        used_ruts = {
            caf_model._normalize_vat(case.partner_id.vat)
            for case in cases
            if case.document_code in {"33", "34"} and case.partner_id
        }
        candidates = certification_set.env["res.partner"].search(
            [
                ("active", "=", True),
                ("parent_id", "=", False),
                ("country_id.code", "=", "CL"),
                ("company_id", "in", [False, certification_set.company_id.id]),
                ("vat", "!=", False),
            ],
            order="customer_rank desc, id",
        )
        eligible = iter(
            partner
            for partner in candidates
            if not self.receiver_problems(partner, certification_set)
        )
        by_identifier = {case.case_identifier: case for case in cases}
        for case in cases:
            if case.document_code in {"33", "34"} and not case.partner_id:
                for partner in eligible:
                    rut = caf_model._normalize_vat(partner.vat)
                    if rut not in used_ruts:
                        case.write({"partner_id": partner.id})
                        used_ruts.add(rut)
                        break
            elif case.document_code in {"56", "61"}:
                target = by_identifier.get(case.reference_case_identifier)
                if (
                    target
                    and target.sequence < case.sequence
                    and target.partner_id
                    and case.partner_id != target.partner_id
                ):
                    case.write({"partner_id": target.partner_id.id})

    @staticmethod
    def receiver_problems(partner, certification_set):
        """Check usable fiscal data, without asking users to classify contacts."""
        company = certification_set.company_id
        caf_model = certification_set.env["l10n_cl.caf.authorization"]
        receiver = partner.commercial_partner_id
        problems = []
        rut = caf_model._normalize_vat(receiver.vat)
        if not caf_model._is_valid_vat(rut) or rut == "666666666":
            problems.append(_("Select a receiver with a valid Chilean RUT."))
        if rut == caf_model._normalize_vat(company.vat):
            problems.append(_("The issuer cannot be its own receiver."))
        if (
            not receiver.active
            or receiver.country_id.code != "CL"
            or (receiver.company_id and receiver.company_id != company)
        ):
            problems.append(
                _("Select an active Chilean contact available to this company.")
            )
        address = " ".join(
            part.strip() for part in (receiver.street, receiver.street2) if part
        )
        fields = (
            (receiver.name, 100),
            (address, 70),
            (receiver.city, 20),
            (receiver.l10n_cl_activity_description, 40),
        )
        if any(
            not (value or "").strip() or len(value.strip()) > limit
            for value, limit in fields
        ):
            problems.append(
                _(
                    "Complete the receiver name, address, commune and activity "
                    "within DTE text limits."
                )
            )
        try:
            for value, _limit in fields:
                (value or "").encode("iso-8859-1")
        except UnicodeEncodeError:
            problems.append(_("Receiver details must be representable in ISO-8859-1."))
        return problems
