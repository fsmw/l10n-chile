# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Turn an Odoo assignment record into the immutable AEC request values."""

import base64
from datetime import datetime
from zoneinfo import ZoneInfo

from cryptography import x509
from cryptography.x509.oid import NameOID

from odoo import fields
from odoo.exceptions import UserError, ValidationError

from odoo.addons.l10n_cl_dte_signing.services.certificate_identity import normalize_rut

from .aec_composer import AecParty, AecRequest

CHILE_TIMEZONE = ZoneInfo("America/Santiago")
_LIMITS = {"name": 100, "address": 80, "email": 80}


def chile_timestamp(moment=None):
    """Return an SII local timestamp (AAAA-MM-DDTHH:MI:SS)."""
    moment = moment or datetime.now(tz=CHILE_TIMEZONE)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo("UTC"))
    return moment.astimezone(CHILE_TIMEZONE).strftime("%Y-%m-%dT%H:%M:%S")


def certificate_holder_name(certificate):
    """Return the certificate subject common name of the authorized signer."""
    try:
        parsed = x509.load_pem_x509_certificate(
            base64.b64decode(certificate.pem_certificate, validate=True)
        )
    except (TypeError, ValueError) as error:
        raise ValidationError(
            certificate.env._("The signing certificate is invalid.")
        ) from error
    names = parsed.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    name = names[0].value.strip() if names else ""
    if not name:
        raise UserError(
            certificate.env._("The signing certificate has no holder name.")
        )
    return name


def partner_party(partner, email, label):
    """Validate one partner as an AEC party within the SII field limits."""
    env = partner.env
    commercial = partner.commercial_partner_id
    address = ", ".join(
        value.strip() for value in (commercial.street, commercial.city) if value
    )
    values = {
        "name": (commercial.name or "").strip(),
        "address": address,
        "email": (email or "").strip(),
    }
    try:
        vat = normalize_rut(commercial.vat)
    except ValidationError as error:
        raise UserError(
            env._("The %(party)s needs a valid Chilean RUT.", party=label)
        ) from error
    if not values["name"] or len(values["address"]) < 5 or len(values["email"]) < 6:
        raise UserError(
            env._(
                "The %(party)s needs a name, a street and city, and an email "
                "address.",
                party=label,
            )
        )
    for key, limit in _LIMITS.items():
        if len(values[key]) > limit:
            raise UserError(
                env._(
                    "The %(party)s %(field)s exceeds %(limit)s characters.",
                    party=label,
                    field=key,
                    limit=limit,
                )
            )
    return AecParty(vat=vat, **values)


def build_aec_request(assignment, dte_bytes, signer_vat, signer_name, timestamp):
    """Return the AEC values for one assignment record."""
    env = assignment.env
    assignor = partner_party(
        assignment.company_id.partner_id,
        assignment.assignor_email,
        env._("assignor company"),
    )
    assignee = partner_party(
        assignment.partner_id, assignment.partner_id.email, env._("assignee")
    )
    return AecRequest(
        dte_bytes=dte_bytes,
        assignor=assignor,
        assignee=assignee,
        signer_vat=normalize_rut(signer_vat),
        signer_name=signer_name,
        amount=assignment.amount,
        last_due_date=fields.Date.to_string(assignment.last_due_date),
        timestamp=timestamp,
        id_suffix=(f"{assignment.document_code}F{assignment.folio}S{assignment.id}"),
        sworn_declaration=(assignment.sworn_declaration or "").strip(),
        other_conditions=(assignment.other_conditions or "").strip(),
        debtor_email=(assignment.debtor_email or "").strip(),
        contact_name=signer_name,
        contact_email=assignor.email,
    )
