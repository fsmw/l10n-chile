# Copyright 2026
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import copy
import hashlib
import re
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import pytz
from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import (
    dsa,
    ec,
    ed448,
    ed25519,
    padding,
    rsa,
)
from lxml import etree

from odoo.modules.module import get_module_path

DSIG_NS = "http://www.w3.org/2000/09/xmldsig#"
DTE_NS = "http://www.sii.cl/SiiDte"
DTE_C14N = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
EXCLUSIVE_C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"
ENVELOPED_SIGNATURE = f"{DSIG_NS}enveloped-signature"
RSA_SHA1 = f"{DSIG_NS}rsa-sha1"
RSA_SHA256 = f"{DSIG_NS}rsa-sha256"
SHA1 = f"{DSIG_NS}sha1"
SHA256 = f"{DSIG_NS}sha256"

# Variantes medidas sobre los 12 DTE reales de `xml-reales/` (ADR-0008):
# - `SignatureValue` verifica con C14N exclusivo aunque el documento declare
#   inclusivo; se conserva el inclusivo como segundo intento porque es la forma
#   que producen las firmas emitidas por este addon.
# - `DigestValue` verifica con C14N exclusivo con namespace (7 emitidos y
#   OPTIMACORP), sin `xmlns="http://www.sii.cl/SiiDte"` (BICE x2, CELER y Flota
#   del Pacífico) o con C14N inclusivo reserializado en contexto (firmas de este
#   addon). La variante que verifica queda registrada como evidencia.
# - `<Transforms>` admite vacío, `[C14N]`, `[enveloped]` y `[enveloped, C14N]`.
#   El corpus real usa `[enveloped-signature]` sin `Transform` de C14N (CELER),
#   que se interpreta como C14N implícito más enveloped.
SUPPORTED_CANONICALIZATION_METHODS = (DTE_C14N, EXCLUSIVE_C14N)
SUPPORTED_TRANSFORMS = (
    (),
    (DTE_C14N,),
    (ENVELOPED_SIGNATURE,),
    (ENVELOPED_SIGNATURE, DTE_C14N),
)


def _xml_parser():
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        huge_tree=False,
    )


class DteXmlValidationError(ValueError):
    """Incoming DTE does not satisfy the supported SII XML profile."""


def _parse_xml(xml_content):
    try:
        root = etree.fromstring(xml_content, parser=_xml_parser())
    except (etree.XMLSyntaxError, TypeError, ValueError) as error:
        raise DteXmlValidationError("El XML no es válido.") from error
    if root.getroottree().docinfo.doctype:
        raise DteXmlValidationError("El XML con DTD no está permitido.")
    return root


@lru_cache(maxsize=1)
def _dte_schema():
    addon_path = get_module_path("l10n_cl_dte_sii")
    if not addon_path:
        raise DteXmlValidationError("No se encontró el esquema SII local de DTE.")
    schema_path = Path(addon_path) / "data" / "xsd" / "DTE_v10.xsd"
    if not schema_path.is_file():
        raise DteXmlValidationError("Falta el esquema local DTE v10 del SII.")
    try:
        schema_document = etree.parse(str(schema_path), parser=_xml_parser())
        return etree.XMLSchema(schema_document)
    except (OSError, etree.XMLSchemaParseError, etree.XMLSyntaxError) as error:
        raise DteXmlValidationError(
            "No se pudo cargar el esquema local DTE v10."
        ) from error


@lru_cache(maxsize=1)
def _boleta_schema():
    addon_path = get_module_path("l10n_cl_dte_sii")
    if not addon_path:
        raise DteXmlValidationError("No se encontró el esquema SII de boletas.")
    schema_path = Path(addon_path) / "data" / "xsd" / "EnvioBOLETA_v11.xsd"
    try:
        return etree.XMLSchema(etree.parse(str(schema_path), parser=_xml_parser()))
    except (OSError, etree.XMLSchemaParseError, etree.XMLSyntaxError) as error:
        raise DteXmlValidationError(
            "No se pudo cargar el esquema de boletas."
        ) from error


def _validate_boleta_dte(dte, code):
    """Validate 39/41 against their inline type in the official v11 envelope XSD."""
    envelope = etree.Element(
        f"{{{DTE_NS}}}EnvioBOLETA",
        nsmap={None: DTE_NS, "ds": DSIG_NS},
        version="1.0",
    )
    set_dte = etree.SubElement(envelope, f"{{{DTE_NS}}}SetDTE", ID="SetDoc")
    cover = etree.SubElement(set_dte, f"{{{DTE_NS}}}Caratula", version="1.0")
    issuer = dte.xpath("string(.//*[local-name()='RUTEmisor'][1])")
    folio_date = dte.xpath("string(.//*[local-name()='FchEmis'][1])") or "2020-01-01"
    for name, value in (
        ("RutEmisor", issuer),
        ("RutEnvia", issuer),
        ("RutReceptor", "60803000-K"),
        ("FchResol", "2020-01-01"),
        ("NroResol", "0"),
        ("TmstFirmaEnv", f"{folio_date}T00:00:00"),
    ):
        etree.SubElement(cover, f"{{{DTE_NS}}}{name}").text = value
    subtotal = etree.SubElement(cover, f"{{{DTE_NS}}}SubTotDTE")
    etree.SubElement(subtotal, f"{{{DTE_NS}}}TpoDTE").text = code
    etree.SubElement(subtotal, f"{{{DTE_NS}}}NroDTE").text = "1"
    set_dte.append(deepcopy(dte))
    signatures = dte.xpath(".//*[local-name()='Signature']")
    if signatures:
        envelope.append(deepcopy(signatures[0]))
    try:
        _boleta_schema().assertValid(envelope)
    except etree.DocumentInvalid as error:
        detail = (
            error.error_log.last_error.message
            if error.error_log.last_error
            else "inválido"
        )
        raise DteXmlValidationError(
            f"La boleta no cumple el XSD v11 del SII: {detail}"
        ) from error


def _canonical(node):
    """C14N inclusivo de un nodo aislado (firmas emitidas por este addon)."""
    return etree.tostring(node, method="c14n", exclusive=False, with_comments=False)


def _exclusive_canonical(node):
    """C14N exclusivo: la variante que verifica en los DTE reales del SII."""
    return etree.tostring(node, method="c14n", exclusive=True, with_comments=False)


def _without_dte_namespace(canonical_xml):
    """Quita `xmlns="http://www.sii.cl/SiiDte"` de la etiqueta raíz canónica.

    Cuatro de los doce documentos reales firman el digest sobre el `Documento`
    serializado sin la declaración de namespace del SII. Sólo se elimina la
    etiqueta de apertura del nodo canónico, nunca el contenido.
    """
    opening_end = canonical_xml.find(b">")
    declaration = f' xmlns="{DTE_NS}"'.encode()
    if opening_end < 0:
        return canonical_xml
    opening = canonical_xml[:opening_end]
    if declaration not in opening:
        return canonical_xml
    return opening.replace(declaration, b"", 1) + canonical_xml[opening_end:]


def _digest_payloads(node):
    """Payloads del digest en el orden de intentos de ADR-0008, sin repetir."""
    exclusive = _exclusive_canonical(node)
    payloads = [("exclusivo", exclusive)]
    without_namespace = _without_dte_namespace(exclusive)
    if without_namespace != exclusive:
        payloads.append(("exclusivo-sin-namespace", without_namespace))
    inclusive = _canonical(node)
    if inclusive not in {payload for _, payload in payloads}:
        payloads.append(("inclusivo", inclusive))
    return payloads


def _one(node, xpath, namespaces=None):
    values = node.xpath(xpath, namespaces=namespaces or {"ds": DSIG_NS})
    if len(values) != 1:
        raise DteXmlValidationError(
            "La firma XML tiene estructura ambigua o incompleta."
        )
    return values[0]


def _decode_b64(value):
    try:
        return base64.b64decode("".join((value or "").split()), validate=True)
    except (ValueError, TypeError) as error:
        raise DteXmlValidationError("La firma XML contiene base64 inválido.") from error


def _enveloped_digest_target(dte, reference_id, namespaces):
    """Copy of the tree without the nested signature, for enveloped signatures."""
    digest_root = copy.deepcopy(dte)
    if digest_root.get("ID") == reference_id:
        copied = digest_root
    else:
        copied_targets = digest_root.xpath(
            ".//*[@ID=$reference_id]", reference_id=reference_id
        )
        if len(copied_targets) != 1:
            raise DteXmlValidationError("El ID de la referencia XMLDSIG no es único.")
        copied = copied_targets[0]
    nested_signatures = copied.xpath(".//ds:Signature", namespaces=namespaces)
    if copied.tag == f"{{{DSIG_NS}}}Signature":
        nested_signatures.insert(0, copied)
    if len(nested_signatures) > 1:
        raise DteXmlValidationError("La firma enveloped no es unívoca.")
    if nested_signatures:
        nested_signatures[0].getparent().remove(nested_signatures[0])
    return copied


def _verify_reference(dte, signed_info, namespaces, evidence=None):
    reference = _one(signed_info, "./ds:Reference", namespaces)
    uri = reference.get("URI", "")
    if not uri.startswith("#") or len(uri) < 2:
        raise DteXmlValidationError("La firma no apunta a un ID interno del DTE.")
    reference_id = uri[1:]
    targets = dte.xpath(".//*[@ID=$reference_id]", reference_id=reference_id)
    if dte.get("ID") == reference_id:
        targets.insert(0, dte)
    if len(targets) != 1:
        raise DteXmlValidationError("El ID de la referencia XMLDSIG no es único.")
    target = targets[0]

    transforms = tuple(
        reference.xpath(
            "./ds:Transforms/ds:Transform/@Algorithm", namespaces=namespaces
        )
    )
    if transforms not in SUPPORTED_TRANSFORMS:
        raise DteXmlValidationError(
            "Las transformaciones de la firma no son compatibles."
        )
    enveloped = ENVELOPED_SIGNATURE in transforms
    signature = _one(dte, ".//ds:Signature", namespaces)
    if signature is target:
        raise DteXmlValidationError(
            "La firma XML referencia un elemento contenedor inválido."
        )
    if target in signature.iterancestors() and not enveloped:
        raise DteXmlValidationError("Falta la transformación enveloped-signature.")

    digest_method = _one(reference, "./ds:DigestMethod", namespaces).get("Algorithm")
    digest_functions = {SHA1: hashlib.sha1, SHA256: hashlib.sha256}
    digest = digest_functions.get(digest_method)
    if not digest:
        raise DteXmlValidationError("El algoritmo digest de XMLDSIG no es compatible.")
    digest_target = (
        _enveloped_digest_target(dte, reference_id, namespaces) if enveloped else target
    )
    expected_digest = _decode_b64(_one(reference, "./ds:DigestValue", namespaces).text)
    for variant, payload in _digest_payloads(digest_target):
        if digest(payload).digest() == expected_digest:
            if evidence is not None:
                evidence["digest_variant"] = variant
                evidence["transforms"] = list(transforms)
            return target
    raise DteXmlValidationError(
        "El contenido del DTE no coincide con su firma XMLDSIG."
    )


def _verify_signed_info(signature, signed_info, namespaces, evidence=None):
    signature_method = _one(signed_info, "./ds:SignatureMethod", namespaces).get(
        "Algorithm"
    )
    signature_hashes = {RSA_SHA1: hashes.SHA1, RSA_SHA256: hashes.SHA256}
    hash_class = signature_hashes.get(signature_method)
    if not hash_class:
        raise DteXmlValidationError("El algoritmo de firma XMLDSIG no es compatible.")
    declared_canonicalization = _one(
        signed_info, "./ds:CanonicalizationMethod", namespaces
    ).get("Algorithm")
    if declared_canonicalization not in SUPPORTED_CANONICALIZATION_METHODS:
        raise DteXmlValidationError("La canonicalización de XMLDSIG no es compatible.")
    certificate_value = _one(
        signature, ".//ds:X509Data/ds:X509Certificate", namespaces
    ).text
    try:
        certificate = x509.load_der_x509_certificate(_decode_b64(certificate_value))
    except (ValueError, TypeError) as error:
        raise DteXmlValidationError(
            "El certificado embebido en XMLDSIG no es válido."
        ) from error
    public_key = certificate.public_key()
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise DteXmlValidationError("La firma XMLDSIG no usa una clave RSA compatible.")
    signature_value = _decode_b64(
        _one(signature, "./ds:SignatureValue", namespaces).text
    )
    # El SII firma el `SignedInfo` aislado: al verificarlo en contexto la forma
    # equivalente es exclusiva. El inclusivo queda como segundo intento para las
    # firmas emitidas por este addon. Ambas variantes son deterministas sobre el
    # mismo subárbol, por lo que no amplían el contenido que puede verificarse.
    for variant, payload in (
        ("exclusivo", _exclusive_canonical(signed_info)),
        ("inclusivo", _canonical(signed_info)),
    ):
        try:
            public_key.verify(
                signature_value,
                payload,
                padding.PKCS1v15(),
                hash_class(),
            )
        except InvalidSignature:
            continue
        if evidence is not None:
            evidence["signature_variant"] = variant
            evidence["declared_canonicalization"] = declared_canonicalization
        return certificate
    raise DteXmlValidationError("La firma criptográfica XMLDSIG no es válida.")


def _verify_signing_certificate_validity(dte, certificate):
    """Check the embedded certificate's validity interval at signed TmstFirma.

    The SII timestamp has no UTC offset; interpret it in Chile's civil timezone.
    This check does not validate the issuer chain or certificate revocation.
    """
    documents = dte.xpath(
        "./*[local-name()='Documento' or local-name()='Liquidacion' "
        "or local-name()='Exportaciones']"
    )
    timestamps = (
        documents[0].xpath("./*[local-name()='TmstFirma']/text()")
        if len(documents) == 1
        else []
    )
    if len(timestamps) != 1 or not timestamps[0].strip():
        raise DteXmlValidationError(
            "El documento firmado debe contener un único TmstFirma."
        )
    try:
        signing_time = datetime.fromisoformat(timestamps[0].strip())
    except ValueError as error:
        raise DteXmlValidationError("El TmstFirma del DTE no es válido.") from error
    signing_times_utc = []
    if signing_time.tzinfo is None:
        zone = pytz.timezone("America/Santiago")
        for is_dst in (True, False):
            local_time = zone.localize(signing_time, is_dst=is_dst)
            signing_time_round_trip = local_time.astimezone(pytz.UTC).astimezone(zone)
            if signing_time_round_trip.replace(tzinfo=None) == signing_time:
                signing_time_utc = local_time.astimezone(pytz.UTC)
                if signing_time_utc not in signing_times_utc:
                    signing_times_utc.append(signing_time_utc)
    else:
        signing_times_utc = [signing_time.astimezone(timezone.utc)]
    if not signing_times_utc:
        raise DteXmlValidationError(
            "El TmstFirma no corresponde a una hora válida de America/Santiago."
        )

    not_before = getattr(certificate, "not_valid_before_utc", None)
    if not_before is None:
        not_before = certificate.not_valid_before.replace(tzinfo=timezone.utc)
    not_after = getattr(certificate, "not_valid_after_utc", None)
    if not_after is None:
        not_after = certificate.not_valid_after.replace(tzinfo=timezone.utc)
    validity_results = [
        not_before <= signing_time_utc <= not_after
        for signing_time_utc in signing_times_utc
    ]
    if any(validity_results) and not all(validity_results):
        raise DteXmlValidationError(
            "No se puede determinar la vigencia: el TmstFirma cae en una hora "
            "ambigua de America/Santiago."
        )
    if not all(validity_results):
        raise DteXmlValidationError(
            "El certificado XMLDSIG no estaba vigente en el TmstFirma declarado."
        )
    return True


def _pem_objects(bundle, loader, label):
    if not bundle:
        raise DteXmlValidationError(f"No está configurado el bundle {label}.")
    blocks = re.findall(
        rb"-----BEGIN [^-]+-----.*?-----END [^-]+-----", bundle, re.DOTALL
    )
    objects = []
    for block in blocks:
        try:
            objects.append(loader(block))
        except (TypeError, ValueError, UnsupportedAlgorithm) as error:
            raise DteXmlValidationError(
                f"El bundle {label} contiene PEM inválido."
            ) from error
    if not objects:
        raise DteXmlValidationError(f"El bundle {label} no contiene objetos PEM.")
    return objects


def _verify_certificate_issued_by(certificate, issuer):
    if certificate.issuer != issuer.subject:
        return False
    public_key = issuer.public_key()
    try:
        if isinstance(public_key, rsa.RSAPublicKey):
            parameters = getattr(certificate, "signature_algorithm_parameters", None)
            public_key.verify(
                certificate.signature,
                certificate.tbs_certificate_bytes,
                parameters or padding.PKCS1v15(),
                certificate.signature_hash_algorithm,
            )
        elif isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(
                certificate.signature,
                certificate.tbs_certificate_bytes,
                ec.ECDSA(certificate.signature_hash_algorithm),
            )
        elif isinstance(public_key, dsa.DSAPublicKey):
            public_key.verify(
                certificate.signature,
                certificate.tbs_certificate_bytes,
                certificate.signature_hash_algorithm,
            )
        elif isinstance(public_key, (ed25519.Ed25519PublicKey, ed448.Ed448PublicKey)):
            public_key.verify(certificate.signature, certificate.tbs_certificate_bytes)
        else:
            return False
    except (InvalidSignature, TypeError, ValueError, UnsupportedAlgorithm):
        return False
    return True


def _certificate_time_valid(certificate, timestamp):
    not_before = getattr(certificate, "not_valid_before_utc", None)
    if not_before is None:
        not_before = certificate.not_valid_before.replace(tzinfo=timezone.utc)
    not_after = getattr(certificate, "not_valid_after_utc", None)
    if not_after is None:
        not_after = certificate.not_valid_after.replace(tzinfo=timezone.utc)
    return not_before <= timestamp <= not_after


def _signed_at(dte):
    timestamp_text = dte.xpath(
        "string(./*[local-name()='Documento' or local-name()='Liquidacion' "
        "or local-name()='Exportaciones']/*[local-name()='TmstFirma'])"
    )
    try:
        signed_at = datetime.fromisoformat(timestamp_text.strip())
        if signed_at.tzinfo is None:
            signed_at = (
                pytz.timezone("America/Santiago")
                .localize(signed_at, is_dst=None)
                .astimezone(timezone.utc)
            )
        else:
            signed_at = signed_at.astimezone(timezone.utc)
    except (ValueError, TypeError, pytz.InvalidTimeError) as error:
        raise DteXmlValidationError(
            "No se pudo interpretar TmstFirma para validar confianza."
        ) from error
    return signed_at


def _validate_issuing_ca(certificate):
    try:
        constraints = certificate.extensions.get_extension_for_class(
            x509.BasicConstraints
        ).value
    except x509.ExtensionNotFound as error:
        raise DteXmlValidationError(
            "El emisor del certificado no declara BasicConstraints."
        ) from error
    if not constraints.ca:
        raise DteXmlValidationError("El certificado emisor no está autorizado como CA.")
    try:
        key_usage = certificate.extensions.get_extension_for_class(x509.KeyUsage)
    except x509.ExtensionNotFound:
        key_usage = None
    if key_usage and not key_usage.value.key_cert_sign:
        raise DteXmlValidationError(
            "El certificado emisor no permite firmar certificados."
        )


def _validate_path_constraints(path):
    for index, certificate in enumerate(path[1:], start=1):
        _validate_issuing_ca(certificate)
        constraints = certificate.extensions.get_extension_for_class(
            x509.BasicConstraints
        ).value
        subordinate_cas = sum(
            1
            for subordinate in path[1:index]
            if subordinate.subject != subordinate.issuer
        )
        if (
            constraints.path_length is not None
            and subordinate_cas > constraints.path_length
        ):
            raise DteXmlValidationError(
                "La cadena excede el pathLength de una autoridad certificadora."
            )


def _build_certificate_path(leaf_certificate, intermediate_cas, trusted_cas, signed_at):
    path = [leaf_certificate]
    current = leaf_certificate
    visited = set()
    while True:
        fingerprint = current.fingerprint(hashes.SHA256())
        if fingerprint in visited:
            raise DteXmlValidationError("La cadena del certificado contiene un ciclo.")
        visited.add(fingerprint)
        anchor = next(
            (
                ca
                for ca in trusted_cas
                if ca.fingerprint(hashes.SHA256()) == fingerprint
            ),
            None,
        )
        if anchor:
            break
        issuers = [
            ca
            for ca in [*intermediate_cas, *trusted_cas]
            if _verify_certificate_issued_by(current, ca)
        ]
        if not issuers:
            raise DteXmlValidationError(
                "No se pudo construir una cadena hasta una autoridad "
                "configurada como confiable."
            )
        issuer = issuers[0]
        _validate_issuing_ca(issuer)
        path.append(issuer)
        current = issuer

    if not _certificate_time_valid(leaf_certificate, signed_at):
        raise DteXmlValidationError(
            "El certificado firmante no estaba vigente en el TmstFirma declarado."
        )
    try:
        key_usage = leaf_certificate.extensions.get_extension_for_class(x509.KeyUsage)
    except x509.ExtensionNotFound:
        key_usage = None
    if key_usage and not (
        key_usage.value.digital_signature or key_usage.value.content_commitment
    ):
        raise DteXmlValidationError(
            "El certificado firmante no permite firmas digitales."
        )
    for certificate in path[1:]:
        if not _certificate_time_valid(certificate, signed_at):
            raise DteXmlValidationError(
                "Un certificado de la cadena no estaba vigente en el "
                "TmstFirma declarado."
            )
    _validate_path_constraints(path)
    return path


def _crl_covers_time(crl, signed_at):
    this_update = getattr(crl, "last_update_utc", None)
    if this_update is None:
        this_update = crl.last_update.replace(tzinfo=timezone.utc)
    next_update = getattr(crl, "next_update_utc", None)
    if next_update is None and crl.next_update:
        next_update = crl.next_update.replace(tzinfo=timezone.utc)
    return this_update <= signed_at and next_update and signed_at <= next_update


def _verify_path_revocation(path, crls, signed_at):
    evidence = []
    for certificate in path[:-1]:
        issuer = next(
            (
                candidate
                for candidate in path[1:]
                if candidate.subject == certificate.issuer
            ),
            None,
        )
        if issuer is None:
            raise DteXmlValidationError(
                "No se encontró el emisor para comprobar revocación."
            )
        applicable = [
            crl
            for crl in crls
            if crl.issuer == issuer.subject
            and _crl_covers_time(crl, signed_at)
            and _verify_certificate_issued_by_crl(crl, issuer)
        ]
        if not applicable:
            raise DteXmlValidationError(
                "No hay una CRL firmada y vigente al TmstFirma para el "
                "emisor del certificado."
            )
        revoked_entries = [
            crl.get_revoked_certificate_by_serial_number(certificate.serial_number)
            for crl in applicable
        ]
        if any(
            entry and _revocation_time(entry) <= signed_at for entry in revoked_entries
        ):
            raise DteXmlValidationError(
                "El certificado estaba revocado en el TmstFirma declarado."
            )
        evidence.append(
            {
                "certificate_serial": format(certificate.serial_number, "x"),
                "issuer_sha256": issuer.fingerprint(hashes.SHA256()).hex(),
                "crl_sha256": [
                    crl.fingerprint(hashes.SHA256()).hex() for crl in applicable
                ],
            }
        )
    return evidence


def _revocation_time(entry):
    revoked_at = getattr(entry, "revocation_date_utc", None)
    if revoked_at is None:
        revoked_at = entry.revocation_date.replace(tzinfo=timezone.utc)
    return revoked_at


def _verify_certificate_trust_and_revocation(
    leaf_certificate, dte, trusted_ca_bundle, crl_bundle, intermediate_ca_bundle=None
):
    """Validate signer path and signed-time revocation against admin data.

    Only explicitly configured CA certificates are trust anchors. Embedded
    certificates are never trusted as roots. Every non-root certificate needs
    a signed CRL that covers the DTE's signed timestamp.
    """
    trusted_cas = _pem_objects(
        trusted_ca_bundle,
        x509.load_pem_x509_certificate,
        "de autoridades certificadoras",
    )
    intermediate_cas = (
        _pem_objects(
            intermediate_ca_bundle,
            x509.load_pem_x509_certificate,
            "de autoridades intermedias",
        )
        if intermediate_ca_bundle
        else []
    )
    crls = _pem_objects(crl_bundle, x509.load_pem_x509_crl, "de listas de revocación")
    signed_at = _signed_at(dte)
    path = _build_certificate_path(
        leaf_certificate, intermediate_cas, trusted_cas, signed_at
    )
    revocation_evidence = _verify_path_revocation(path, crls, signed_at)
    return {
        "signed_at": signed_at.isoformat(),
        "signer_sha256": leaf_certificate.fingerprint(hashes.SHA256()).hex(),
        "chain_sha256": [cert.fingerprint(hashes.SHA256()).hex() for cert in path],
        "trusted_root_sha256": path[-1].fingerprint(hashes.SHA256()).hex(),
        "revocation_checks": revocation_evidence,
    }


def _verify_certificate_issued_by_crl(crl, issuer):
    public_key = issuer.public_key()
    try:
        try:
            key_usage = issuer.extensions.get_extension_for_class(x509.KeyUsage)
        except x509.ExtensionNotFound:
            key_usage = None
        if key_usage and not key_usage.value.crl_sign:
            return False
        if isinstance(public_key, rsa.RSAPublicKey):
            parameters = getattr(crl, "signature_algorithm_parameters", None)
            public_key.verify(
                crl.signature,
                crl.tbs_certlist_bytes,
                parameters or padding.PKCS1v15(),
                crl.signature_hash_algorithm,
            )
        elif isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(
                crl.signature,
                crl.tbs_certlist_bytes,
                ec.ECDSA(crl.signature_hash_algorithm),
            )
        elif isinstance(public_key, dsa.DSAPublicKey):
            public_key.verify(
                crl.signature, crl.tbs_certlist_bytes, crl.signature_hash_algorithm
            )
        elif isinstance(public_key, (ed25519.Ed25519PublicKey, ed448.Ed448PublicKey)):
            public_key.verify(crl.signature, crl.tbs_certlist_bytes)
        else:
            return False
    except (InvalidSignature, TypeError, ValueError, UnsupportedAlgorithm):
        return False
    return True


def validate_dte_trust_materials(trusted_ca_bundle, intermediate_ca_bundle, crl_bundle):
    """Validate uploaded PEM material without making any network requests.

    Empty bundles are allowed so an administrator can stage the configuration;
    incoming DTE validation still fails closed until required material exists.
    """
    trusted_cas = (
        _pem_objects(
            trusted_ca_bundle,
            x509.load_pem_x509_certificate,
            "de autoridades certificadoras",
        )
        if trusted_ca_bundle
        else []
    )
    intermediate_cas = (
        _pem_objects(
            intermediate_ca_bundle,
            x509.load_pem_x509_certificate,
            "de autoridades intermedias",
        )
        if intermediate_ca_bundle
        else []
    )
    crls = (
        _pem_objects(crl_bundle, x509.load_pem_x509_crl, "de listas de revocación")
        if crl_bundle
        else []
    )
    certificates = [*trusted_cas, *intermediate_cas]
    fingerprints = [cert.fingerprint(hashes.SHA256()) for cert in certificates]
    if len(fingerprints) != len(set(fingerprints)):
        raise DteXmlValidationError(
            "Un certificado CA está duplicado entre raíces e intermedias."
        )
    for certificate in certificates:
        _validate_issuing_ca(certificate)

    for crl in crls:
        last_update = getattr(crl, "last_update_utc", None)
        if last_update is None:
            last_update = crl.last_update.replace(tzinfo=timezone.utc)
        next_update = getattr(crl, "next_update_utc", None)
        if next_update is None and crl.next_update:
            next_update = crl.next_update.replace(tzinfo=timezone.utc)
        if not next_update or next_update <= last_update:
            raise DteXmlValidationError(
                "Cada CRL debe declarar un intervalo de vigencia válido."
            )
        issuers = [cert for cert in certificates if cert.subject == crl.issuer]
        if not issuers:
            raise DteXmlValidationError(
                "La CRL refiere a un emisor que no está en los bundles CA."
            )
        if not any(
            _verify_certificate_issued_by_crl(crl, issuer) for issuer in issuers
        ):
            raise DteXmlValidationError(
                "La firma de una CRL no coincide con ninguna CA configurada."
            )
    return True


def _verify_signature(dte, evidence=None):
    namespaces = {"ds": DSIG_NS}
    signatures = dte.xpath(".//ds:Signature", namespaces=namespaces)
    if len(signatures) != 1:
        raise DteXmlValidationError(
            "El DTE debe contener exactamente una firma XMLDSIG."
        )
    signature = signatures[0]
    signed_info = _one(signature, "./ds:SignedInfo", namespaces)
    target = _verify_reference(dte, signed_info, namespaces, evidence)
    certificate = _verify_signed_info(signature, signed_info, namespaces, evidence)
    fiscal_documents = dte.xpath(
        "./*[local-name()='Documento' or local-name()='Liquidacion' "
        "or local-name()='Exportaciones']"
    )
    if len(fiscal_documents) != 1 or target not in (dte, fiscal_documents[0]):
        raise DteXmlValidationError(
            "La firma XMLDSIG no cubre el DTE ni su Documento fiscal."
        )
    return certificate


def validate_dte_xml(
    xml_content,
    trusted_ca_bundle=None,
    crl_bundle=None,
    intermediate_ca_bundle=None,
    require_trust=False,
    return_evidence=False,
):
    """Validate DTE profile, signature and optionally configured PKI evidence.

    ``require_trust`` enables fail-closed chain and signed-time CRL validation
    against administrator-provided bundles. It never trusts the embedded signer
    certificate as a root and does not establish SII acceptance or authorization.
    """
    root = _parse_xml(xml_content)
    dtes = (
        [root]
        if etree.QName(root).localname == "DTE"
        else root.xpath(".//*[local-name()='DTE']")
    )
    if not dtes:
        raise DteXmlValidationError("El XML no contiene documentos DTE.")
    validation_evidence = []
    for dte in dtes:
        signature_evidence = {}
        certificate = validate_dte_element(dte, evidence=signature_evidence)
        entry = dict(signature_evidence)
        if require_trust:
            entry.update(
                _verify_certificate_trust_and_revocation(
                    certificate,
                    dte,
                    trusted_ca_bundle,
                    crl_bundle,
                    intermediate_ca_bundle,
                )
            )
        validation_evidence.append(entry)
    return validation_evidence if return_evidence else True


def validate_dte_element(dte, evidence=None):
    """Validate one already-parsed DTE without rewriting signed source bytes.

    Cuando se entrega `evidence`, se registra la variante de canonicalización
    que verificó el `SignatureValue` y el digest (ADR-0008).
    """
    if not isinstance(dte, etree._Element) or etree.QName(dte).localname != "DTE":
        raise DteXmlValidationError("El elemento entregado no es un DTE.")
    code = dte.xpath("string(.//*[local-name()='TipoDTE'][1])")
    try:
        if code in {"39", "41"}:
            _validate_boleta_dte(dte, code)
        else:
            _dte_schema().assertValid(dte)
    except etree.DocumentInvalid as error:
        detail = (
            error.error_log.last_error.message
            if error.error_log.last_error
            else "inválido"
        )
        raise DteXmlValidationError(
            f"El DTE no cumple el XSD v10 del SII: {detail}"
        ) from error
    certificate = _verify_signature(dte, evidence=evidence)
    _verify_signing_certificate_validity(dte, certificate)
    return certificate
