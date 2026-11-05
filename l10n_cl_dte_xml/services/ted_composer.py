# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService


class DteTedComposer:
    """Serialize and CAF-sign the shared TED primitive for DTE_v10 documents."""

    def compose_ted(self, snapshot, caf_authorization, signature_service=None):
        """Build the SII TED using the same byte layout used by invoice DTEs."""
        from .xml_composer import _extract_caf_bytes, _tag

        caf_authorization.ensure_one()
        document_code = str(snapshot["document_code"])
        if (
            not document_code.isdigit()
            or caf_authorization.document_code != document_code
        ):
            raise ValidationError(_("The CAF type must match the DTE type."))
        folio = int(snapshot["folio"])
        if not caf_authorization.folio_start <= folio <= caf_authorization.folio_end:
            raise ValidationError(_("The folio is outside the CAF range."))
        if caf_authorization.company_id.id != snapshot["company_id"]:
            raise ValidationError(_("The snapshot and CAF companies do not match."))
        caf_authorization._check_caf_integrity()
        caf_bytes = caf_authorization._decode_content(caf_authorization.caf_file)
        caf_fragment = _extract_caf_bytes(caf_bytes)
        issuer = snapshot["issuer"]
        receiver = snapshot["receiver"]
        totals = snapshot["totals"]
        first_line = snapshot["lines"][0]
        dd_xml = b"".join(
            (
                b"<DD>",
                _tag("RE", issuer["vat"]),
                _tag("TD", document_code),
                _tag("F", folio),
                _tag("FE", snapshot["issue_date"]),
                _tag("RR", receiver["vat"]),
                _tag("RSR", receiver["name"]),
                _tag("MNT", totals["total"]),
                _tag("IT1", first_line["name"][:40]),
                caf_fragment,
                _tag("TSTED", snapshot["ted_timestamp"]),
                b"</DD>",
            )
        )
        signer = signature_service or SignatureService()
        frmt = signer.sign_ted(caf_authorization.company_id, caf_authorization, dd_xml)
        wrapped_frmt = (
            base64.encodebytes(base64.b64decode(frmt)).decode("ascii").strip()
        )
        return (
            b'<TED version="1.0">'
            + dd_xml
            + b'<FRMT algoritmo="SHA1withRSA">'
            + wrapped_frmt.encode("ascii")
            + b"</FRMT></TED>"
        )
