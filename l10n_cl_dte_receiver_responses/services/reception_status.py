# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Map the inbox's local import results to RecepcionEnvio status codes."""

from .exchange_composer import DOCUMENT_GLOSSES, ENVELOPE_GLOSSES
from .source_facts import EXCHANGE_DOCUMENT_CODES


def envelope_status(batch, envelope, company_vat):
    """Return (EstadoRecepEnv, RecepEnvGlosa) for an imported file."""
    if envelope["receiver_vat"] and envelope["receiver_vat"] != company_vat:
        code = "3"
    elif batch.state == "imported":
        code = "0"
    elif batch.schema_state == "invalid":
        code = "1"
    elif batch.signature_state == "invalid":
        code = "2"
    else:
        code = "99"
    return code, ENVELOPE_GLOSSES[code]


def document_status(line, facts, envelope, company_vat):
    """Return (EstadoRecepDTE, RecepDTEGlosa) for one DTE of the file."""
    if line.result == "imported":
        code = "0"
    elif line.result == "duplicate":
        code = "4"
    elif line.signature_state == "invalid":
        code = "1"
    elif envelope["issuer_vat"] and facts["issuer_vat"] != envelope["issuer_vat"]:
        code = "2"
    elif facts["receiver_vat"] != company_vat:
        code = "3"
    else:
        code = "99"
    return code, DOCUMENT_GLOSSES[code]


def reportable(facts):
    """Whether the schema can carry this DTE in a response detail."""
    return (
        facts["document_code"] in EXCHANGE_DOCUMENT_CODES
        and facts["folio"].isdigit()
        and int(facts["folio"]) > 0
        and bool(facts["issue_date"])
        and bool(facts["issuer_vat"])
        and bool(facts["receiver_vat"])
    )


def reception_lines(batch, envelope, company_vat):
    """Return the RecepcionDTE facts for each reportable DTE of the file."""
    lines = []
    for line in batch.line_ids.sorted("sequence"):
        position = line.sequence - 1
        if not 0 <= position < len(envelope["documents"]):
            continue
        facts = envelope["documents"][position]
        if not reportable(facts):
            continue
        state, gloss = document_status(line, facts, envelope, company_vat)
        lines.append(
            dict(facts, state=state, gloss=gloss, document=line.incoming_document_id)
        )
    return lines
