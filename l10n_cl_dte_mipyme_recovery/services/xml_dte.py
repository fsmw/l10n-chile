# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Safe, byte-preserving extraction and validation of recovered DTE XML."""

import hashlib
import re
from dataclasses import dataclass

from lxml import etree

from .identity import DteIdentity, normalize_dte_type, normalize_folio, normalize_rut

DTE_NS = "http://www.sii.cl/SiiDte"
DSIG_NS = "http://www.w3.org/2000/09/xmldsig#"
_DTE_BYTES = re.compile(
    rb"<(?:[A-Za-z_][\w.-]*:)?DTE\b[^>]*>.*?</(?:[A-Za-z_][\w.-]*:)?DTE\s*>",
    re.DOTALL,
)


@dataclass(frozen=True)
class DteCandidate:
    identity: DteIdentity
    raw_xml: bytes
    digest: str
    element: object = None


@dataclass(frozen=True)
class DteAccountingData:
    recipient_rut: str
    recipient_foreign_id: str
    recipient_country_code: str
    recipient_name: str
    recipient_activity: str
    recipient_street: str
    recipient_city: str
    issue_date: str
    currency_name: str
    company_currency_name: str
    company_total_amount: float
    details: tuple
    references: tuple
    exempt_amount: float
    net_amount: float
    vat_amount: float
    total_amount: float


def _xml_parser():
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        huge_tree=False,
        remove_comments=False,
    )


def _as_bytes(payload):
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        # Fixtures and test doubles may provide a decoded ISO-8859-1 body.  Do
        # not use UTF-8 here: the recovered bytes are fiscal evidence.
        return payload.encode("ISO-8859-1")
    raise ValueError("La respuesta MiPyme no contiene bytes XML.")


def _local(element):
    return etree.QName(element).localname


def _find(element, name):
    return element.xpath(".//*[local-name()=$name][1]", name=name)


def _raw_dte_parts(payload):
    raw = _as_bytes(payload)
    try:
        root = etree.fromstring(raw, parser=_xml_parser())
    except etree.XMLSyntaxError:
        try:
            root = etree.fromstring(
                raw,
                parser=etree.XMLParser(
                    encoding="ISO-8859-1",
                    resolve_entities=False,
                    no_network=True,
                    load_dtd=False,
                    huge_tree=False,
                    remove_comments=False,
                ),
            )
        except (etree.XMLSyntaxError, ValueError) as error:
            raise ValueError(f"XML malformado: {error}") from error

    if _local(root) == "DTE":
        return [(raw, root)]

    parts = []
    matches = list(_DTE_BYTES.finditer(raw))
    nodes = root.xpath('.//*[local-name()="DTE"]')
    if len(matches) != len(nodes):
        # A namespace form that cannot be safely sliced is rejected instead of
        # serializing it and silently changing signed bytes.
        raise ValueError("No se pudieron preservar los bytes de cada DTE.")
    for match, node in zip(matches, nodes, strict=False):
        parts.append((match.group(0), node))
    return parts


def extract_candidates(payload):
    """Extract DTEs from a SetDTE/envelope without reserializing them."""
    candidates = []
    for raw, dte in _raw_dte_parts(payload):
        issuer = _find(dte, "RUTEmisor")
        dte_type = _find(dte, "TipoDTE")
        folio = _find(dte, "Folio")
        if not issuer or not dte_type or not folio:
            continue
        try:
            identity = DteIdentity(
                issuer_rut=normalize_rut(issuer[0].text),
                dte_type=normalize_dte_type(dte_type[0].text),
                folio=normalize_folio(folio[0].text),
            )
        except ValueError:
            continue
        candidates.append(
            DteCandidate(identity, raw, hashlib.sha256(raw).hexdigest(), dte)
        )
    return candidates


def validate_candidate(candidate, expected):
    """Validate structure, identity, signature and TED before persistence."""
    expected_identity = DteIdentity(
        normalize_rut(expected.issuer_rut),
        normalize_dte_type(expected.dte_type),
        normalize_folio(expected.folio),
    )
    if candidate.identity != expected_identity:
        raise ValueError("El DTE no coincide exactamente con la identidad solicitada.")
    try:
        # ``candidate.element`` was parsed from the complete SetDTE, whose
        # declaration carries ISO-8859-1 when applicable.  Re-parsing the
        # sliced DTE bytes would incorrectly assume UTF-8 because the slice
        # intentionally has no XML declaration.
        root = candidate.element
        if root is None:
            try:
                root = etree.fromstring(candidate.raw_xml, parser=_xml_parser())
            except etree.XMLSyntaxError:
                parser = etree.XMLParser(
                    encoding="ISO-8859-1",
                    resolve_entities=False,
                    no_network=True,
                    load_dtd=False,
                    huge_tree=False,
                    remove_comments=False,
                )
                root = etree.fromstring(candidate.raw_xml, parser=parser)
    except (etree.XMLSyntaxError, ValueError) as error:
        raise ValueError(f"XML malformado: {error}") from error
    if root is None or _local(root) != "DTE":
        raise ValueError("El XML recuperado no contiene un DTE individual.")
    if not root.xpath('.//*[local-name()="TED"]'):
        raise ValueError("El DTE recuperado no contiene TED.")
    if not root.xpath('.//*[local-name()="Signature"]'):
        raise ValueError("El DTE recuperado no contiene firma digital.")
    return candidate


def accounting_data(candidate):
    """Extract accounting values without changing the signed XML bytes."""
    root = candidate.element

    def text(name, parent=root):
        nodes = parent.xpath(".//*[local-name()=$name][1]", name=name)
        return (nodes[0].text or "").strip() if nodes else ""

    def amount(name):
        value = text(name)
        try:
            return float(value or 0)
        except ValueError as error:
            raise ValueError(f"El monto XML {name} no es numérico.") from error

    details = []
    for detail in root.xpath('.//*[local-name()="Detalle"]'):
        quantity = text("QtyItem", detail) or "1"
        price = text("PrcItem", detail) or "0"
        try:
            quantity = float(quantity)
            price = float(price)
            line_amount = float(text("MontoItem", detail) or quantity * price)
        except ValueError as error:
            raise ValueError(
                "La línea del DTE contiene cantidad, precio o importe inválido."
            ) from error
        if quantity <= 0 or line_amount < 0:
            raise ValueError("La línea del DTE contiene cantidad o importe inválido.")
        details.append(
            {
                "name": text("NmbItem", detail) or "DTE recuperado",
                "quantity": quantity,
                "price_unit": line_amount / quantity,
                "amount": line_amount,
                "exempt": bool(text("IndExe", detail)),
            }
        )
    if not details:
        details.append(
            {
                "name": "DTE recuperado",
                "quantity": 1.0,
                "price_unit": amount("MntTotal"),
                "amount": amount("MntTotal"),
                "exempt": bool(amount("MntExe")),
            }
        )
    references = []
    for reference in root.xpath('.//*[local-name()="Referencia"]'):
        references.append(
            {
                "document_type": text("TpoDocRef", reference),
                "folio": text("FolioRef", reference),
                "date": text("FchRef", reference),
                "code": text("CodRef", reference),
                "reason": text("RazonRef", reference),
            }
        )
    totals = root.xpath('.//*[local-name()="Totales"]')
    other_currency = root.xpath('.//*[local-name()="OtraMoneda"]')
    return DteAccountingData(
        recipient_rut=text("RUTRecep"),
        recipient_foreign_id=text("NumId"),
        recipient_country_code=text("Nacionalidad") or text("CodPaisRecep"),
        recipient_name=text("RznSocRecep"),
        recipient_activity=text("GiroRecep"),
        recipient_street=text("DirRecep"),
        recipient_city=text("CmnaRecep"),
        issue_date=text("FchEmis"),
        currency_name=text("TpoMoneda", totals[0]) if totals else "",
        company_currency_name=(
            text("TpoMoneda", other_currency[0]) if other_currency else ""
        ),
        company_total_amount=amount("MntTotOtrMnda"),
        details=tuple(details),
        references=tuple(references),
        exempt_amount=amount("MntExe"),
        net_amount=amount("MntNeto"),
        vat_amount=amount("IVA"),
        total_amount=amount("MntTotal"),
    )
