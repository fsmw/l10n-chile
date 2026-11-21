# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Compose the unsigned SII Archivo Electrónico de Cesión (AEC).

The assigned DTE is embedded byte for byte: the composer serializes the AEC
skeleton with a placeholder element and splices the stored signed DTE into it,
so its own XMLDSig keeps verifying. Three signature placeholders mark where
the signer inserts the DTECedido, Cesion and AEC signatures.
"""

import re
from dataclasses import dataclass

from lxml import etree

from odoo.exceptions import ValidationError
from odoo.tools.translate import LazyTranslate

from odoo.addons.l10n_cl_dte_core.tools.xml_safety import safe_fromstring
from odoo.addons.l10n_cl_dte_signing.services.signature_service import (
    SII_XML_DECLARATION,
)

_lt = LazyTranslate(__name__, default_lang="en_US")

SII = "http://www.sii.cl/SiiDte"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
SCHEMA_LOCATION = f"{SII} AEC_v10.xsd"
ASSIGNABLE_DOCUMENT_CODES = frozenset({"33", "34"})
DTE_MARKER = "AecDteCedidoPlaceholder"
SIGNATURE_MARKERS = (
    "AecDteCedidoSignaturePlaceholder",
    "AecCesionSignaturePlaceholder",
    "AecDocumentSignaturePlaceholder",
)
_DECLARATION_RE = re.compile(rb"^\s*<\?xml[^?]*\?>\s*")
_ENCODING_RE = re.compile(rb"encoding\s*=\s*[\"']([A-Za-z0-9._-]+)[\"']")


def _tag(name):
    return f"{{{SII}}}{name}"


def marker_bytes(name):
    """Return the exact serialization of one placeholder element."""
    return f"<{name}/>".encode("ascii")


@dataclass(frozen=True)
class AecParty:
    """One party of the assignment as printed in the AEC."""

    vat: str
    name: str
    address: str
    email: str


@dataclass(frozen=True)
class AecRequest:
    """Every value the AEC states, captured before composition."""

    dte_bytes: bytes
    assignor: AecParty
    assignee: AecParty
    signer_vat: str
    signer_name: str
    amount: int
    last_due_date: str
    timestamp: str
    id_suffix: str
    sequence: int = 1
    sworn_declaration: str = ""
    other_conditions: str = ""
    debtor_email: str = ""
    contact_name: str = ""
    contact_email: str = ""


def dte_element_bytes(dte_bytes):
    """Return the signed DTE element bytes without the XML declaration."""
    if not isinstance(dte_bytes, bytes) or not dte_bytes.strip():
        raise ValidationError(_lt("The assigned DTE XML is empty."))
    declaration = _DECLARATION_RE.match(dte_bytes)
    if declaration:
        encoding = _ENCODING_RE.search(declaration.group(0))
        name = encoding.group(1).decode("ascii").upper() if encoding else "UTF-8"
        if name not in {"ISO-8859-1", "LATIN1", "LATIN-1"} and not dte_bytes.isascii():
            raise ValidationError(
                _lt("The assigned DTE must be encoded in ISO-8859-1 to be embedded.")
            )
        dte_bytes = dte_bytes[declaration.end() :]
    return dte_bytes.strip()


def _single(root, path):
    nodes = root.xpath(path)
    if len(nodes) != 1:
        raise ValidationError(
            _lt("The assigned DTE has a missing or ambiguous %(path)s.", path=path)
        )
    value = nodes[0] if isinstance(nodes[0], str) else nodes[0].text
    return (value or "").strip()


def read_dte_identity(dte_bytes):
    """Extract the fiscal identity that the Cesion IdDTE must repeat."""
    body = dte_element_bytes(dte_bytes)
    try:
        root = safe_fromstring(body, encoding="ISO-8859-1")
    except (etree.XMLSyntaxError, ValueError) as error:
        raise ValidationError(
            _lt("The assigned DTE is not well-formed XML.")
        ) from error
    if root.tag != _tag("DTE"):
        raise ValidationError(_lt("The assigned XML is not an SII DTE."))
    prefix = "./*[local-name()='Documento']/*[local-name()='Encabezado']/"
    identity = {
        "TipoDTE": _single(
            root, prefix + "*[local-name()='IdDoc']/*[local-name()='TipoDTE']"
        ),
        "Folio": _single(
            root, prefix + "*[local-name()='IdDoc']/*[local-name()='Folio']"
        ),
        "FchEmis": _single(
            root, prefix + "*[local-name()='IdDoc']/*[local-name()='FchEmis']"
        ),
        "RUTEmisor": _single(
            root, prefix + "*[local-name()='Emisor']/*[local-name()='RUTEmisor']"
        ),
        "RUTReceptor": _single(
            root, prefix + "*[local-name()='Receptor']/*[local-name()='RUTRecep']"
        ),
        "RznSocRecep": _single(
            root, prefix + "*[local-name()='Receptor']/*[local-name()='RznSocRecep']"
        ),
        "MntTotal": _single(
            root, prefix + "*[local-name()='Totales']/*[local-name()='MntTotal']"
        ),
    }
    if identity["TipoDTE"] not in ASSIGNABLE_DOCUMENT_CODES:
        raise ValidationError(
            _lt("Only electronic invoices (DTE 33 and 34) can be assigned.")
        )
    if not identity["Folio"].isdigit() or not identity["MntTotal"].isdigit():
        raise ValidationError(_lt("The assigned DTE folio or total is invalid."))
    return identity


def default_sworn_declaration(assignor, assignee, debtor):
    """Return the sworn statement wording published by the SII (Ley 19.983).

    Each party is a ``(name, rut)`` pair.
    """
    return (
        f"Se declara bajo juramento que {assignor[0]}, RUT {assignor[1]} ha "
        f"puesto a disposición del cesionario {assignee[0]}, RUT {assignee[1]}, "
        "el o los documentos donde constan los recibos de las mercaderías "
        "entregadas o servicios prestados, entregados por parte del deudor de "
        f"la factura {debtor[0]}, RUT {debtor[1]}, de acuerdo a lo "
        "establecido en la Ley N° 19.983."
    )[:512]


class AecComposer:
    """Build the unsigned AEC with placeholders for its three signatures."""

    def compose(self, request):
        """Return ISO-8859-1 AEC bytes with the exact DTE and signature markers."""
        identity = read_dte_identity(request.dte_bytes)
        self._check_request(request, identity)
        root = etree.Element(_tag("AEC"), nsmap={None: SII, "xsi": XSI})
        root.set("version", "1.0")
        root.set(f"{{{XSI}}}schemaLocation", SCHEMA_LOCATION)
        document = etree.SubElement(
            root, _tag("DocumentoAEC"), ID=f"AEC{request.id_suffix}"
        )
        self._add_cover(document, request)
        assignments = etree.SubElement(document, _tag("Cesiones"))
        self._add_assigned_document(assignments, request)
        self._add_assignment(assignments, request, identity)
        etree.SubElement(root, _tag(SIGNATURE_MARKERS[2]))
        body = etree.tostring(root, encoding="ISO-8859-1", xml_declaration=False)
        # SII's sample places the declaration and the AEC start tag on their
        # own lines, as its EnvioDTE upload parser requires.
        opening_end = body.index(b">") + 1
        body = body[:opening_end] + b"\n" + body[opening_end:]
        marker = marker_bytes(DTE_MARKER)
        if body.count(marker) != 1:
            raise ValidationError(_lt("The AEC placeholder for the DTE is ambiguous."))
        body = body.replace(marker, dte_element_bytes(request.dte_bytes))
        return SII_XML_DECLARATION + b"\n" + body + b"\n"

    @staticmethod
    def _check_request(request, identity):
        if request.sequence != 1:
            raise ValidationError(
                _lt("Only the first assignment of an issued invoice is supported.")
            )
        if identity["RUTEmisor"] != request.assignor.vat:
            raise ValidationError(
                _lt("Only the issuer of the DTE can assign it as first assignor.")
            )
        if request.assignee.vat in {request.assignor.vat, identity["RUTReceptor"]}:
            raise ValidationError(
                _lt("The assignee must differ from the assignor and the debtor.")
            )
        if not 0 < request.amount <= int(identity["MntTotal"]):
            raise ValidationError(
                _lt("The assigned amount must be positive and up to the DTE total.")
            )
        if request.last_due_date < identity["FchEmis"]:
            raise ValidationError(
                _lt("The last due date cannot precede the DTE issue date.")
            )
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", request.id_suffix or ""):
            raise ValidationError(_lt("The AEC element identifier is invalid."))

    @staticmethod
    def _add(parent, name, value, required=True):
        if value in (None, False, ""):
            if required:
                raise ValidationError(
                    _lt("The AEC field %(field)s is required.", field=name)
                )
            return None
        node = etree.SubElement(parent, _tag(name))
        node.text = str(value)
        return node

    def _add_cover(self, document, request):
        cover = etree.SubElement(document, _tag("Caratula"), version="1.0")
        self._add(cover, "RutCedente", request.assignor.vat)
        self._add(cover, "RutCesionario", request.assignee.vat)
        self._add(cover, "NmbContacto", request.contact_name[:40], required=False)
        self._add(cover, "MailContacto", request.contact_email, required=False)
        self._add(cover, "TmstFirmaEnvio", request.timestamp)

    def _add_assigned_document(self, assignments, request):
        assigned = etree.SubElement(assignments, _tag("DTECedido"), version="1.0")
        document = etree.SubElement(
            assigned, _tag("DocumentoDTECedido"), ID=f"DTEC{request.id_suffix}"
        )
        etree.SubElement(document, _tag(DTE_MARKER))
        self._add(document, "TmstFirma", request.timestamp)
        etree.SubElement(assigned, _tag(SIGNATURE_MARKERS[0]))

    def _add_assignment(self, assignments, request, identity):
        assignment = etree.SubElement(assignments, _tag("Cesion"), version="1.0")
        document = etree.SubElement(
            assignment, _tag("DocumentoCesion"), ID=f"CES{request.id_suffix}"
        )
        self._add(document, "SeqCesion", request.sequence)
        dte_id = etree.SubElement(document, _tag("IdDTE"))
        for name in ("TipoDTE", "RUTEmisor", "RUTReceptor", "Folio", "FchEmis"):
            self._add(dte_id, name, identity[name])
        self._add(dte_id, "MntTotal", identity["MntTotal"])
        self._add_assignor(document, request)
        self._add_party(document, "Cesionario", request.assignee)
        self._add(document, "MontoCesion", request.amount)
        self._add(document, "UltimoVencimiento", request.last_due_date)
        self._add(document, "OtrasCondiciones", request.other_conditions, False)
        self._add(document, "eMailDeudor", request.debtor_email, required=False)
        self._add(document, "TmstCesion", request.timestamp)
        etree.SubElement(assignment, _tag(SIGNATURE_MARKERS[1]))

    def _add_assignor(self, document, request):
        assignor = self._add_party(document, "Cedente", request.assignor)
        authorized = etree.SubElement(assignor, _tag("RUTAutorizado"))
        self._add(authorized, "RUT", request.signer_vat)
        self._add(authorized, "Nombre", request.signer_name[:40])
        self._add(
            assignor, "DeclaracionJurada", request.sworn_declaration, required=False
        )

    def _add_party(self, document, name, party):
        node = etree.SubElement(document, _tag(name))
        self._add(node, "RUT", party.vat)
        self._add(node, "RazonSocial", party.name)
        self._add(node, "Direccion", party.address)
        self._add(node, "eMail", party.email)
        return node
