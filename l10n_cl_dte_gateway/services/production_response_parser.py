# Copyright 2026 Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Bounded and strict parsers for production SII transport responses."""

import re

from lxml import etree

from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_signing.services.certificate_identity import normalize_rut

from .certification_response_parser import soap_payload as _certification_soap_payload

MAX_RESPONSE_BYTES = 2_000_000


def parse_xml(data):
    if not isinstance(data, bytes) or not data or len(data) > MAX_RESPONSE_BYTES:
        raise ValidationError(
            _("The SII XML response is empty or exceeds the allowed size.")
        )
    try:
        root = etree.fromstring(
            data,
            etree.XMLParser(
                resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False
            ),
        )
    except etree.XMLSyntaxError as error:
        raise ValidationError(_("The SII response is not valid XML.")) from error
    if root.getroottree().docinfo.doctype:
        raise ValidationError(
            _("SII XML responses must not contain a document type declaration.")
        )
    return root


def _values(node, name):
    return node.xpath(".//*[local-name()=$name]/text()", name=name)


def text(node, name, required=True):
    values = _values(node, name)
    if len(values) != 1 or not values[0].strip():
        if not required and not values:
            return ""
        raise ValidationError(
            _("The SII response has a missing or ambiguous %(field)s.")
            % {"field": name}
        )
    return values[0].strip()


def soap_payload(data, operation):
    parse_xml(data)
    return _certification_soap_payload(data, operation)


def parse_upload(response, expected_issuer, expected_sender):
    root = parse_xml(response)
    if etree.QName(root).localname != "RECEPCIONDTE":
        raise ValidationError(_("SII did not return an upload receipt."))
    issuer = normalize_rut(text(root, "RUTCOMPANY"))
    sender = normalize_rut(text(root, "RUTSENDER"))
    if issuer != normalize_rut(expected_issuer) or sender != normalize_rut(
        expected_sender
    ):
        raise ValidationError(
            _("The SII upload receipt belongs to a different issuer or sender.")
        )
    code = text(root, "STATUS")
    if not re.fullmatch(r"\d{1,2}", code):
        raise ValidationError(
            _("The SII response has a missing or ambiguous %(field)s.")
            % {"field": "STATUS"}
        )
    if code != "0":
        return {
            "state": "rejected",
            "track_id": False,
            "transport_code": code,
            "raw_response": response,
        }
    track_id = text(root, "TRACKID")
    if not re.fullmatch(r"\d{1,10}", track_id) or int(track_id) <= 0:
        raise ValidationError(_("SII returned an invalid TrackID."))
    return {
        "state": "submitted",
        "track_id": track_id,
        "transport_code": code,
        "raw_response": response,
    }


def parse_status(response, expected_track_id):
    root = soap_payload(response, "getEstUp")
    returned = text(root, "TRACKID", required=False)
    if returned and returned != expected_track_id:
        raise ValidationError(
            _("The SII status response belongs to a different TrackID.")
        )
    code = text(root, "ESTADO")
    result = {
        "state": "outcome_unknown",
        "track_id": expected_track_id,
        "transport_code": code,
        "raw_response": response,
        "counts": [],
    }
    if code in {"RSC", "RFR", "RCT"}:
        result["state"] = "rejected"
    elif code in {"SOK", "CRT", "FOK", "PDR", "PRD", "REC"}:
        result["state"] = "processing"
    elif code == "EPR":
        for body in root.xpath(
            ".//*[./*[local-name()='TIPO_DOCTO'] and ./*[local-name()='INFORMADOS']]"
        ):
            row = {}
            aliases = {
                "TIPO_DOCTO": ("TIPO_DOCTO",),
                "INFORMADOS": ("INFORMADOS",),
                "ACEPTADOS": ("ACEPTADOS", "ACEPTA"),
                "RECHAZADOS": ("RECHAZADOS", "RECHAZO"),
                "REPAROS": ("REPAROS", "REPARO"),
            }
            for key, names in aliases.items():
                values = [value for name in names for value in _values(body, name)]
                if key in {"TIPO_DOCTO", "INFORMADOS"} and len(values) != 1:
                    raise ValidationError(
                        _("The SII document counters are inconsistent.")
                    )
                if key not in {"TIPO_DOCTO", "INFORMADOS"} and not values:
                    values = ["0"]
                if any(not value.strip().isdigit() for value in values):
                    raise ValidationError(
                        _("The SII document counters must be nonnegative integers.")
                    )
                row[key] = (
                    values[0].strip()
                    if key == "TIPO_DOCTO"
                    else sum(map(lambda value: int(value.strip()), values))
                )
            if (
                row["INFORMADOS"]
                != row["ACEPTADOS"] + row["RECHAZADOS"] + row["REPAROS"]
            ):
                raise ValidationError(_("The SII document counters do not reconcile."))
            result["counts"].append(row)
        if result["counts"]:
            total = sum(row["INFORMADOS"] for row in result["counts"])
            if total:
                rejected = sum(row["RECHAZADOS"] for row in result["counts"])
                repairs = sum(row["REPAROS"] for row in result["counts"])
                result["state"] = (
                    "rejected"
                    if rejected == total
                    else "accepted_with_observations"
                    if repairs
                    else "accepted"
                )
    return result
