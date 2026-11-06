# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Reconcile an SII DTEMAIL result XML with the submitted envelope."""

import hashlib
import re
from collections import Counter, defaultdict

from lxml import etree

from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_signing.services.certificate_identity import normalize_rut

from .certification_response_parser import parse_xml


def reconcile_result(response_bytes, envelope_bytes, company_vat, track_id):
    """Return operator-supplied per-DTE outcomes and structural discrepancies."""
    response = parse_xml(response_bytes)
    envelope = parse_xml(envelope_bytes)
    if _local_name(response) != "RESULTADO_ENVIO":
        raise ValidationError(_("The uploaded XML is not an SII send result."))
    if _local_name(envelope) != "EnvioDTE":
        raise ValidationError(_("The stored request is not an SII envelope."))

    identification = _one(response, "IDENTIFICACION")
    reported_issuer = _rut(_one_text(identification, "RUTEMISOR"))
    expected_issuer = _rut(company_vat)
    reported_sender = _rut(_one_text(identification, "RUTENVIA"))
    cover = _one(_one(envelope, "SetDTE"), "Caratula")
    expected_sender = _rut(_one_text(cover, "RutEnvia"))
    reported_track_id = _one_text(identification, "TRACKID")
    if not re.fullmatch(r"\d{1,20}", reported_track_id):
        raise ValidationError(_("The SII result has an invalid TrackID."))
    if reported_issuer != expected_issuer:
        raise ValidationError(_("The SII result belongs to another issuer."))
    if reported_sender != expected_sender:
        raise ValidationError(_("The SII result belongs to another sender."))
    if reported_track_id != track_id:
        raise ValidationError(_("The SII result TrackID does not match the envelope."))

    expected = _expected_documents(envelope)
    expected_by_key = {
        (item["document_type"], item["folio"]): item for item in expected
    }
    expected_counts = Counter(item["document_type"] for item in expected)
    response_state = _one_text(identification, "ESTADO")
    if not response_state.startswith("EPR"):
        raise ValidationError(
            _("The SII result is not a processed envelope result (EPR).")
        )

    discrepancies = []
    statistics = _statistics(response)
    details = _revision_details(response)
    detail_by_key = defaultdict(list)
    for detail in details:
        key = (detail["document_type"], detail["folio"])
        detail_by_key[key].append(detail)
        if key not in expected_by_key:
            discrepancies.append(
                _("SII returned an unexpected DTE %(type)s/%(folio)s.")
                % {"type": key[0], "folio": key[1]}
            )

    result_lines = {}
    for code in sorted(set(expected_counts) | set(statistics)):
        code_details = [item for item in details if item["document_type"] == code]
        code_keys = [key for key in expected_by_key if key[0] == code]
        summary = statistics.get(code)
        if summary is None:
            discrepancies.append(
                _("SII omitted document statistics for DTE type %s.") % code
            )
            _set_indeterminate(result_lines, expected_by_key, code_keys)
            continue
        if not _statistics_match(
            code, expected_counts.get(code, 0), summary, discrepancies
        ):
            _set_indeterminate(result_lines, expected_by_key, code_keys)
            continue
        if not _details_match_statistics(
            code,
            code_keys,
            code_details,
            summary,
            detail_by_key,
            expected_by_key,
            discrepancies,
        ):
            _set_indeterminate(result_lines, expected_by_key, code_keys)
            continue
        _classify_documents(code_keys, expected_by_key, detail_by_key, result_lines)

    for code in statistics.keys() - expected_counts.keys():
        discrepancies.append(_("SII reported an unexpected DTE type %s.") % code)
    for key in expected_by_key.keys() - result_lines.keys():
        result_lines[key] = _line_values(expected_by_key[key], "indeterminate", "", "")

    ordered_lines = [
        result_lines[(item["document_type"], item["folio"])] for item in expected
    ]
    accepted = sum(line["state"] == "accepted" for line in ordered_lines)
    rejected = sum(line["state"] == "rejected" for line in ordered_lines)
    repairs = sum(
        line["state"] == "accepted_with_observations" for line in ordered_lines
    )
    indeterminate = sum(line["state"] == "indeterminate" for line in ordered_lines)
    return {
        "source_digest": hashlib.sha256(response_bytes).hexdigest(),
        "sii_state": response_state,
        "track_id": reported_track_id,
        "reconciliation_state": "reconciled" if not discrepancies else "mismatch",
        "expected_count": len(expected),
        "accepted_count": accepted,
        "rejected_count": rejected,
        "repair_count": repairs,
        "indeterminate_count": indeterminate,
        "discrepancies": discrepancies,
        "line_values": ordered_lines,
    }


def _statistics_match(code, expected_count, summary, discrepancies):
    informed = summary["informed"]
    balanced = (
        informed == summary["accepted"] + summary["rejected"] + summary["repairs"]
    )
    if informed != expected_count:
        discrepancies.append(
            _(
                "DTE type %(type)s: envelope has %(expected)s, "
                "SII reported %(reported)s."
            )
            % {"type": code, "expected": expected_count, "reported": informed}
        )
    if not balanced:
        discrepancies.append(_("SII totals do not balance for DTE type %s.") % code)
    return informed == expected_count and balanced


def _details_match_statistics(
    code, keys, details, summary, detail_by_key, expected_by_key, discrepancies
):
    expected_exceptions = summary["rejected"] + summary["repairs"]
    if len(details) != expected_exceptions:
        discrepancies.append(
            _(
                "DTE type %(type)s: SII reported %(count)s exceptions but "
                "provided %(details)s detail rows."
            )
            % {"type": code, "count": expected_exceptions, "details": len(details)}
        )
        return False
    valid = all(
        (item["document_type"], item["folio"]) in expected_by_key for item in details
    )
    for key in keys:
        matches = detail_by_key.get(key, [])
        if len(matches) > 1:
            discrepancies.append(
                _("SII returned duplicate details for DTE %(type)s/%(folio)s.")
                % {"type": key[0], "folio": key[1]}
            )
            valid = False
        elif matches and matches[0]["state_code"] not in {"RCH", "RPR"}:
            discrepancies.append(
                _("SII returned an unsupported result code for DTE %(type)s/%(folio)s.")
                % {"type": key[0], "folio": key[1]}
            )
            valid = False
    rejected = sum(item["state_code"] == "RCH" for item in details)
    repairs = sum(item["state_code"] == "RPR" for item in details)
    if rejected != summary["rejected"] or repairs != summary["repairs"]:
        discrepancies.append(
            _("SII detail rows do not match the totals for DTE type %s.") % code
        )
        valid = False
    return valid


def _classify_documents(keys, expected_by_key, detail_by_key, result_lines):
    for key in keys:
        matches = detail_by_key.get(key, [])
        if matches:
            state_code = matches[0]["state_code"]
            state = "rejected" if state_code == "RCH" else "accepted_with_observations"
            detail_text = matches[0]["detail"]
        else:
            state_code = "ACEPTA"
            state = "accepted"
            detail_text = ""
        result_lines[key] = _line_values(
            expected_by_key[key], state, state_code, detail_text
        )


def _expected_documents(envelope):
    set_dte = _one(envelope, "SetDTE")
    cover = _one(set_dte, "Caratula")
    envelope_issuer = _rut(_one_text(cover, "RutEmisor"))
    documents = []
    seen = set()
    for dte in set_dte.xpath("./*[local-name()='DTE']"):
        document = _one(dte, "Documento")
        header = _one(document, "Encabezado")
        id_doc = _one(header, "IdDoc")
        issuer = _rut(_one_text(_one(header, "Emisor"), "RUTEmisor"))
        key = (_one_text(id_doc, "TipoDTE"), _one_text(id_doc, "Folio"))
        if not re.fullmatch(r"\d{1,3}", key[0]) or not re.fullmatch(
            r"\d{1,10}", key[1]
        ):
            raise ValidationError(
                _("The submitted envelope has an invalid DTE identity.")
            )
        if key in seen:
            raise ValidationError(_("The submitted envelope repeats a DTE identity."))
        seen.add(key)
        if issuer != envelope_issuer:
            raise ValidationError(_("The envelope contains a different DTE issuer."))
        receiver = _one(header, "Receptor")
        documents.append(
            {
                "document_type": key[0],
                "folio": key[1],
                "issuer_vat": issuer,
                "receiver_vat": _one_text(receiver, "RUTRecep"),
                "issue_date": _one_text(id_doc, "FchEmis"),
                "document_digest": hashlib.sha256(
                    # Hash the full signed DTE element, not a reconstructed header.
                    etree.tostring(dte, encoding="UTF-8", with_tail=False)
                ).hexdigest(),
            }
        )
    if not documents:
        raise ValidationError(_("The submitted envelope has no DTE documents."))
    return documents


def _statistics(root):
    statistics = {}
    for subtotal in root.xpath(
        ".//*[local-name()='ESTADISTICA']/*[local-name()='SUBTOTAL']"
    ):
        code = _one_text(subtotal, "TIPODOC")
        if not re.fullmatch(r"\d{1,3}", code) or code in statistics:
            raise ValidationError(
                _("The SII result has duplicate or invalid statistics.")
            )
        statistics[code] = {
            "informed": _count(subtotal, ("INFORMADO", "INFORMADOS"), required=True),
            "accepted": _count(subtotal, ("ACEPTA", "ACEPTADOS")),
            "rejected": _count(subtotal, ("RECHAZO", "RECHAZADOS")),
            "repairs": _count(subtotal, ("REPARO", "REPAROS")),
        }
    if not statistics:
        raise ValidationError(_("The SII result has no document statistics."))
    return statistics


def _revision_details(root):
    details = []
    for item in root.xpath(
        ".//*[local-name()='REVISIONENVIO']/*[local-name()='REVISIONDTE']"
    ):
        state = _one_text(item, "ESTADO")
        details.append(
            {
                "document_type": _one_text(item, "TIPODTE"),
                "folio": _one_text(item, "FOLIO"),
                "state_code": state.split("-", 1)[0].strip(),
                "detail": "\n".join(
                    text.strip()
                    for text in item.xpath("./*[local-name()='DETALLE']/text()")
                    if text.strip()
                ),
            }
        )
    return details


def _count(node, names, required=False):
    found = []
    for name in names:
        found.extend(node.xpath("./*[local-name()=$name]/text()", name=name))
    if not found and not required:
        return 0
    if len(found) != 1 or not found[0].strip().isdigit():
        raise ValidationError(_("The SII result has an invalid document count."))
    return int(found[0].strip())


def _one(node, name):
    matches = node.xpath("./*[local-name()=$name]", name=name)
    if len(matches) != 1:
        raise ValidationError(
            _("The SII result has a missing or ambiguous %(field)s.") % {"field": name}
        )
    return matches[0]


def _one_text(node, name):
    value = "".join(_one(node, name).itertext()).strip()
    if not value:
        raise ValidationError(
            _("The SII result has a missing or ambiguous %(field)s.") % {"field": name}
        )
    return value


def _rut(value):
    return normalize_rut(value)


def _local_name(node):
    return node.tag.rsplit("}", 1)[-1]


def _line_values(expected, state, state_code, detail):
    return {
        **expected,
        "state": state,
        "sii_state_code": state_code,
        "sii_detail": detail,
    }


def _set_indeterminate(result_lines, expected_by_key, keys):
    for key in keys:
        result_lines[key] = _line_values(expected_by_key[key], "indeterminate", "", "")
