# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import re

from lxml import etree

from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_signing.services.certificate_identity import normalize_rut


def parse_xml(content):
    """Read bounded external XML without DTDs, entities or network resolution."""
    if not isinstance(content, bytes) or not content or len(content) > 2_000_000:
        raise ValidationError(
            _("The SII XML response is empty or exceeds the allowed size.")
        )
    try:
        root = etree.fromstring(
            content,
            etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True),
        )
    except etree.XMLSyntaxError as error:
        raise ValidationError(_("The SII response is not valid XML.")) from error
    if root.getroottree().docinfo.doctype:
        raise ValidationError(
            _("SII XML responses must not contain a document type declaration.")
        )
    return root


def text(root, name, required=True):
    values = root.xpath(".//*[local-name()=$name]/text()", name=name)
    if len(values) != 1 or not values[0].strip():
        if not required and not values:
            return ""
        raise ValidationError(
            _("The SII response has a missing or ambiguous %(field)s.")
            % {"field": name}
        )
    return values[0].strip()


def soap_payload(content, operation):
    root = parse_xml(content)
    if root.tag != "{http://schemas.xmlsoap.org/soap/envelope/}Envelope":
        raise ValidationError(_("SII returned an unexpected SOAP envelope."))
    if root.xpath(".//*[local-name()='Fault']"):
        raise ValidationError(
            _("SII returned a SOAP fault. The operation was not confirmed.")
        )
    # SOAP has already decoded the inner XML string to Unicode. Its original
    # byte-encoding declaration must not reinterpret the new UTF-8 bytes.
    payload = re.sub(
        r"(<\?xml[^>]*encoding\s*=\s*)[\"'][^\"']+[\"']",
        r'\1"UTF-8"',
        text(root, operation + "Return"),
        count=1,
    )
    return parse_xml(payload.encode("UTF-8"))


def parse_upload(content, issuer, sender):
    root = parse_xml(content)
    if etree.QName(root).localname != "RECEPCIONDTE":
        raise ValidationError(_("SII did not return an upload receipt."))

    if normalize_rut(text(root, "RUTCOMPANY")) != normalize_rut(
        issuer
    ) or normalize_rut(text(root, "RUTSENDER")) != normalize_rut(sender):
        raise ValidationError(
            _("The SII upload receipt belongs to a different issuer or sender.")
        )
    code = text(root, "STATUS")
    if code != "0":
        return {
            "state": "rejected",
            "track_id": "",
            "transport_code": code,
            "raw_response": content,
        }
    track = text(root, "TRACKID")
    if not track.isdigit() or int(track) <= 0:
        raise ValidationError(_("SII returned an invalid TrackID."))
    return {
        "state": "submitted",
        "track_id": track,
        "transport_code": code,
        "raw_response": content,
    }


def parse_status(content, track_id):
    root = soap_payload(content, "getEstUp")
    returned_track = text(root, "TRACKID", required=False)
    if returned_track and returned_track != track_id:
        raise ValidationError(
            _("The SII status response belongs to a different TrackID.")
        )
    code = text(root, "ESTADO")
    result = {
        "state": "outcome_unknown",
        "track_id": track_id,
        "transport_code": code,
        "raw_response": content,
        "counts": [],
    }
    if code in {"RSC", "RFR", "RCT"}:
        result["state"] = "rejected"
    elif code in {"SOK", "CRT", "FOK", "PDR", "PRD", "REC"}:
        result["state"] = "processing"
    elif code == "EPR" and returned_track:
        for body in root.xpath(
            ".//*[./*[local-name()='TIPO_DOCTO'] and ./*[local-name()='INFORMADOS']]"
        ):
            columns = {
                name: body.xpath("./*[local-name()=$name]/text()", name=name)
                for name in (
                    "TIPO_DOCTO",
                    "INFORMADOS",
                    "ACEPTADOS",
                    "RECHAZADOS",
                    "REPAROS",
                )
            }
            if len({len(values) for values in columns.values()}) != 1:
                raise ValidationError(_("The SII document counters are inconsistent."))
            for values in zip(*columns.values(), strict=True):
                if not all(value.strip().isdigit() for value in values):
                    raise ValidationError(
                        _("The SII document counters must be nonnegative integers.")
                    )
                row = dict(zip(columns, map(int, values), strict=True))
                if (
                    row["INFORMADOS"]
                    != row["ACEPTADOS"] + row["RECHAZADOS"] + row["REPAROS"]
                ):
                    raise ValidationError(
                        _("The SII document counters do not reconcile.")
                    )
                result["counts"].append(row)
        counts = result["counts"]
        if counts and sum(row["INFORMADOS"] for row in counts) > 0:
            rejected = sum(row["RECHAZADOS"] for row in counts)
            informed = sum(row["INFORMADOS"] for row in counts)
            observed = sum(row["REPAROS"] for row in counts)
            result["state"] = (
                ("rejected" if rejected == informed else "partially_accepted")
                if rejected
                else ("accepted_with_observations" if observed else "accepted")
            )
    return result
