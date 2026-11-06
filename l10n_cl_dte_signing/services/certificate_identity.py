# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from cryptography import x509
from cryptography.x509.oid import NameOID
from stdnum.cl import rut

from odoo import _
from odoo.exceptions import ValidationError

# Chilean certificate holder RUT, used by accredited local certificate issuers.
HOLDER_RUT_OID = x509.ObjectIdentifier("1.3.6.1.4.1.8321.1")


def normalize_rut(value):
    value = rut.compact(value or "").upper().lstrip("0")
    if not rut.is_valid(value):
        raise ValidationError(_("A valid Chilean RUT is required."))
    return value[:-1] + "-" + value[-1]


def _der_string(data):
    """Decode one bounded DER DirectoryString without accepting trailing data."""
    if len(data) < 2 or data[0] not in (0x0C, 0x13, 0x16):
        raise ValueError("Unsupported certificate identity string")
    length, offset = data[1], 2
    if length & 0x80:
        size = length & 0x7F
        if not 0 < size <= 2 or len(data) < 2 + size:
            raise ValueError("Invalid DER string length")
        length, offset = int.from_bytes(data[2 : 2 + size], "big"), 2 + size
    if length > 64 or offset + length != len(data):
        raise ValueError("Invalid certificate identity size")
    return data[offset:].decode("ascii")


def certificate_sender_rut(certificate):
    """Identify the certificate holder, never the company owning the Odoo record."""
    candidates = [
        attribute.value
        for attribute in certificate.subject.get_attributes_for_oid(
            NameOID.SERIAL_NUMBER
        )
    ]
    try:
        names = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
    except x509.ExtensionNotFound:
        names = []
    try:
        candidates.extend(
            _der_string(name.value)
            for name in names
            if isinstance(name, x509.OtherName) and name.type_id == HOLDER_RUT_OID
        )
        identities = {normalize_rut(value) for value in candidates}
    except (ValueError, ValidationError):
        identities = set()
    if len(identities) != 1:
        raise ValidationError(
            _("The signing certificate must identify one valid Chilean sender RUT.")
        )
    return identities.pop()
