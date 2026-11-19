# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import copy
import hashlib
from collections import Counter

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from lxml import etree
from odoo.addons.l10n_cl_dte_core.tools.xml_safety import (
    safe_fromstring,
    safe_xml_schema,
)
from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import _extract_dd_bytes
from odoo.exceptions import ValidationError
from odoo.tools.misc import file_path

from .incoming_parser import SII_NS, extract_incoming_dtes, parse_incoming_dte

DS_NS = "http://www.w3.org/2000/09/xmldsig#"
C14N_URI = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
ENVELOPED_SIGNATURE_URI = DS_NS + "enveloped-signature"
# The verified Signature is a direct child of DTE/EnvioDTE and references its
# sibling Documento/SetDTE, so enveloped-signature never removes digested bytes.
# Comment-preserving, XPath and reordered chains stay unsupported.
SUPPORTED_TRANSFORM_CHAINS = frozenset(
    {
        (),
        (C14N_URI,),
        (ENVELOPED_SIGNATURE_URI,),
        (ENVELOPED_SIGNATURE_URI, C14N_URI),
    }
)

# SII added two optional IdDoc fields on 2017-05-31 (Formato DTE v2.5, fields
# 11 and 12) that the vendored DTE_v10.xsd predates. They sit right after
# MntBruto and before FmaPago. Received DTEs are checked against these rules in
# Python and then schema-validated without them; emission is unaffected.
TRANSACTION_TYPE_CODES = {
    "TpoTranCompra": frozenset("1234567"),
    "TpoTranVenta": frozenset("123"),
}
IDDOC_FIELDS_BEFORE_TRANSACTION_TYPES = frozenset(
    {
        "TipoDTE",
        "Folio",
        "FchEmis",
        "IndNoRebaja",
        "TipoDespacho",
        "IndTraslado",
        "TpoImpresion",
        "IndServicio",
        "MntBruto",
    }
)
TRANSACTION_TYPE_SCHEMAS = frozenset({"DTE_v10.xsd", "EnvioDTE_v10.xsd"})


def xml_bytes(element):
    """Keep inherited namespaces and declare encoding for signature verification."""
    return etree.tostring(
        element, encoding="ISO-8859-1", xml_declaration=True, with_tail=False
    )


class IncomingValidationService:
    """Validate local evidence without asserting certificate trust or SII origin."""

    def __init__(self, env):
        self.env = env
        self.signer = SignatureService()
        # The shared service's translated errors need the receiving user's context.
        self.signer.env = env
        self.schemas = {}

    def inspect(self, content):
        result = {
            "schema_name": False,
            "diagnostics": [],
            "documents": [],
            "schema_state": "not_checked",
            "signature_state": "not_checked",
        }
        try:
            root, nodes = extract_incoming_dtes(content, self.env._)
        except ValidationError as error:
            result["diagnostics"].append(self._error("structure", str(error)))
            return result
        result["kind"] = etree.QName(root).localname
        # A re-wrapped export: validate the original envelope, not the wrapper.
        result["wrapped_envelope"] = root.getparent() is not None
        if result["wrapped_envelope"]:
            result["diagnostics"].append(
                self._warning(
                    "structure",
                    self.env._(
                        "The file re-wraps the original envelope; only the "
                        "original envelope and its DTEs were verified."
                    ),
                )
            )
        for position, node in enumerate(nodes, 1):
            document = self._inspect_document(node)
            document["position"] = position
            result["documents"].append(document)
        if result["kind"] == "DTE":
            result.update(
                {
                    key: result["documents"][0][key]
                    for key in ("schema_state", "signature_state", "schema_name")
                }
            )
        else:
            module = (
                "l10n_cl_dte_boleta"
                if result["kind"] == "EnvioBOLETA"
                else "l10n_cl_dte_xml"
            )
            schema = (
                "EnvioBOLETA_v11_compat.xsd"
                if result["kind"] == "EnvioBOLETA"
                else "EnvioDTE_v10.xsd"
            )
            result["schema_name"] = schema
            result["schema_state"] = self._schema(
                root, module, schema, result["diagnostics"]
            )
            signature_start = len(result["diagnostics"])
            try:
                if not self._check_signature_shape(root, result["diagnostics"]):
                    raise ValidationError(
                        self.env._("The envelope signature structure is unsupported.")
                    )
                self.signer.verify_envelope_signature(
                    xml_bytes(root) if result["wrapped_envelope"] else content
                )
                result["signature_state"] = "valid"
            except ValidationError as error:
                result["signature_state"] = "invalid"
                result["diagnostics"].append(self._error("signature", str(error)))
            # Each DTE carries its own signature and TED. An envelope signature
            # failure stays recorded but does not block fully verified DTEs.
            if result["signature_state"] == "invalid" and self._documents_verified(
                result["documents"]
            ):
                for item in result["diagnostics"][signature_start:]:
                    if item["stage"] == "signature":
                        item["severity"] = "warning"
            self._check_envelope(root, result)
        return result

    def _inspect_document(self, node):
        content = xml_bytes(node)
        result = {
            "xml": content,
            "diagnostics": [],
            "schema_state": "not_checked",
            "signature_state": "not_checked",
            "ted_state": "not_checked",
            "signer_fingerprint": False,
        }
        try:
            result["identity"] = parse_incoming_dte(content, translate=self.env._)
            caf_model = self.env["l10n_cl.caf.authorization"]
            for field in ("issuer_vat", "receiver_vat"):
                if not caf_model._is_valid_vat(
                    caf_model._normalize_vat(result["identity"][field])
                ):
                    raise ValidationError(
                        self.env._("The DTE contains an invalid Chilean RUT.")
                    )
        except ValidationError as error:
            result["diagnostics"].append(self._error("identity", str(error)))
        code = result.get("identity", {}).get("document_code")
        boleta = code in {"39", "41"}
        result["schema_name"] = "BoletaDTE_v11.xsd" if boleta else "DTE_v10.xsd"
        result["schema_state"] = self._schema(
            node,
            "l10n_cl_dte_boleta" if boleta else "l10n_cl_dte_xml",
            "BoletaDTE_v11.xsd" if boleta else "DTE_v10.xsd",
            result["diagnostics"],
        )
        shape_supported = False
        try:
            shape_supported = self._check_signature_shape(node, result["diagnostics"])
            if not shape_supported:
                raise ValidationError(
                    self.env._("The DTE signature structure is unsupported.")
                )
            self.signer.verify_signature(content)
            result["signature_state"] = "valid"
            certificates = node.findall(
                f"{{{DS_NS}}}Signature/{{{DS_NS}}}KeyInfo/{{{DS_NS}}}X509Data/{{{DS_NS}}}X509Certificate"
            )
            if len(certificates) == 1:
                certificate = x509.load_der_x509_certificate(
                    base64.b64decode(
                        "".join((certificates[0].text or "").split()), validate=True
                    )
                )
                result["signer_fingerprint"] = certificate.fingerprint(
                    hashes.SHA256()
                ).hex()
        except (ValidationError, ValueError) as error:
            result["signature_state"] = "invalid"
            message = str(error)
            if shape_supported and self._changed_after_signing(content):
                message = self.env._(
                    "The DTE and its signature block both changed after signing, "
                    "for example because another tool reformatted the file. "
                    "Request the original XML from the issuer or from SII."
                )
            result["diagnostics"].append(self._error("signature", message))
        try:
            self._check_ted(node, content, result.get("identity", {}))
            result["ted_state"] = "valid"
        except ValidationError as error:
            result["ted_state"] = "invalid"
            result["diagnostics"].append(self._error("ted", str(error)))
        # Canonical identity content detects conflicting revisions independently
        # of harmless ancestor namespace declarations introduced by an envelope.
        result["identity_digest"] = hashlib.sha256(
            etree.tostring(node, method="c14n", exclusive=True)
        ).hexdigest()
        return result

    def _check_ted(self, node, content, identity):
        ted = node.find(f"{{{SII_NS}}}Documento/{{{SII_NS}}}TED")
        if ted is None:
            raise ValidationError(self.env._("The DTE has no TED."))
        dd = ted.find(f"{{{SII_NS}}}DD")
        caf = dd.find(f"{{{SII_NS}}}CAF") if dd is not None else None
        frmt = ted.findtext(f"{{{SII_NS}}}FRMT")
        if dd is None or caf is None or not frmt:
            raise ValidationError(self.env._("The TED is missing DD, CAF or FRMT."))
        self.signer.verify_ted(_extract_dd_bytes(content), frmt, caf)
        self._check_caf_identity(caf, identity)
        for field, tag in (
            ("issuer_vat", "RE"),
            ("document_code", "TD"),
            ("folio", "F"),
            ("issue_date", "FE"),
            ("receiver_vat", "RR"),
        ):
            expected = str(identity.get(field, ""))
            actual = dd.findtext(f"{{{SII_NS}}}{tag}") or ""
            if field.endswith("vat"):
                expected, actual = normalize_vat(expected), normalize_vat(actual)
            if expected != actual:
                raise ValidationError(
                    self.env._("The TED identity does not match the DTE header.")
                )
        if dd.findtext(f"{{{SII_NS}}}MNT") != node.findtext(
            f"{{{SII_NS}}}Documento/{{{SII_NS}}}Encabezado/{{{SII_NS}}}Totales/{{{SII_NS}}}MntTotal"
        ):
            raise ValidationError(
                self.env._("The TED total does not match the DTE header.")
            )

    def _check_caf_identity(self, caf, identity):
        data = caf.find(f"{{{SII_NS}}}DA")
        if data is None:
            raise ValidationError(self.env._("The TED CAF has no authorization data."))
        start = data.findtext(f"{{{SII_NS}}}RNG/{{{SII_NS}}}D") or ""
        end = data.findtext(f"{{{SII_NS}}}RNG/{{{SII_NS}}}H") or ""
        if (
            normalize_vat(data.findtext(f"{{{SII_NS}}}RE") or "")
            != normalize_vat(identity.get("issuer_vat", ""))
            or data.findtext(f"{{{SII_NS}}}TD") != identity.get("document_code")
            or not start.isdigit()
            or not end.isdigit()
            or not int(start) <= identity.get("folio", 0) <= int(end)
        ):
            raise ValidationError(
                self.env._("The TED CAF does not match the issuer, type or folio.")
            )

    def _schema(self, node, module, filename, diagnostics):
        try:
            path = file_path(f"{module}/static/xsd/{filename}")
        except FileNotFoundError:
            path = None
        if not path:
            diagnostics.append(
                self._error(
                    "schema",
                    self.env._(
                        "The required local XML schema is unavailable: %s", filename
                    ),
                )
            )
            return "unsupported"
        if path not in self.schemas:
            try:
                self.schemas[path] = safe_xml_schema(path)
            except (OSError, etree.XMLSyntaxError, etree.XMLSchemaParseError):
                diagnostics.append(
                    self._error(
                        "schema",
                        self.env._(
                            "The required local XML schema cannot be loaded: %s",
                            filename,
                        ),
                    )
                )
                return "unsupported"
        schema = self.schemas[path]
        if filename in TRANSACTION_TYPE_SCHEMAS:
            node = self._without_transaction_types(node, diagnostics)
            if node is None:
                return "invalid"
        try:
            schema.assertValid(node)
        except etree.DocumentInvalid as error:
            for entry in list(error.error_log)[:20]:
                diagnostics.append(
                    {
                        "stage": "schema",
                        "message": entry.message,
                        "line": entry.line,
                        "column": entry.column,
                    }
                )
            return "invalid"
        return "valid"

    def _without_transaction_types(self, node, diagnostics):
        """Check SII transaction types and return a copy without them.

        Return ``node`` itself when no IdDoc carries them, or ``None`` after
        adding a schema diagnostic when one breaks the SII format.
        """
        id_docs = [
            id_doc
            for id_doc in node.iter(f"{{{SII_NS}}}IdDoc")
            if any(
                id_doc.find(f"{{{SII_NS}}}{name}") is not None
                for name in TRANSACTION_TYPE_CODES
            )
        ]
        if not id_docs:
            return node
        for id_doc in id_docs:
            error = self._transaction_type_error(id_doc)
            if error:
                diagnostics.append(self._error("schema", error))
                return None
        stripped = copy.deepcopy(node)
        for name in TRANSACTION_TYPE_CODES:
            for element in list(stripped.iter(f"{{{SII_NS}}}{name}")):
                element.getparent().remove(element)
        return stripped

    def _transaction_type_error(self, id_doc):
        names = [etree.QName(child).localname for child in id_doc]
        found = [name for name in names if name in TRANSACTION_TYPE_CODES]
        allowed = (["TpoTranCompra"], ["TpoTranVenta"], list(TRANSACTION_TYPE_CODES))
        if found not in allowed:
            return self.env._(
                "TpoTranCompra and TpoTranVenta may appear once each, in that order."
            )
        start = names.index(found[0])
        end = start + len(found)
        before = IDDOC_FIELDS_BEFORE_TRANSACTION_TYPES
        if (
            names[start:end] != found
            or not set(names[:start]) <= before
            or set(names[end:]) & before
        ):
            return self.env._(
                "TpoTranCompra and TpoTranVenta must follow MntBruto and precede "
                "FmaPago in IdDoc."
            )
        for name in found:
            value = (id_doc.findtext(f"{{{SII_NS}}}{name}") or "").strip()
            if value not in TRANSACTION_TYPE_CODES[name]:
                return self.env._(
                    "%(field)s has a code outside the SII table: %(value)s",
                    field=name,
                    value=value,
                )
        return False

    def _check_signature_shape(self, root, diagnostics):
        previous_count = len(diagnostics)
        references = root.findall(
            f"{{{DS_NS}}}Signature/{{{DS_NS}}}SignedInfo/{{{DS_NS}}}Reference"
        )
        if len(references) != 1:
            diagnostics.append(
                self._error(
                    "signature",
                    self.env._("One local XML signature reference is required."),
                )
            )
        for reference in references:
            transforms = reference.findall(
                f"{{{DS_NS}}}Transforms/{{{DS_NS}}}Transform"
            )
            chain = tuple(item.get("Algorithm") for item in transforms)
            if chain not in SUPPORTED_TRANSFORM_CHAINS:
                diagnostics.append(
                    self._error(
                        "signature",
                        self.env._("The XML signature transform is unsupported."),
                    )
                )
        return len(diagnostics) == previous_count

    def _changed_after_signing(self, content):
        """Return whether both the payload digest and SignedInfo fail to verify.

        Altered content under an intact SignedInfo keeps the plain digest error;
        only a file whose signed blocks were both re-serialized gets the hint.
        """
        root = safe_fromstring(content)
        signature = root.find(f"{{{DS_NS}}}Signature")
        signed_info = signature.find(f"{{{DS_NS}}}SignedInfo")
        try:
            public_key = self.signer._public_key_from_key_info(signature)
            signature_bytes = base64.b64decode(
                "".join(signature.findtext(f"{{{DS_NS}}}SignatureValue").split()),
                validate=True,
            )
            expected_digest = base64.b64decode(
                "".join(
                    signed_info.findtext(
                        f"{{{DS_NS}}}Reference/{{{DS_NS}}}DigestValue"
                    ).split()
                ),
                validate=True,
            )
        except (AttributeError, ValidationError, ValueError):
            return False
        if not isinstance(
            public_key, rsa.RSAPublicKey
        ) or self.signer._verify_signed_info(public_key, signature_bytes, signed_info):
            return False
        try:
            self.signer._verify_document_digest(
                self.signer._select_payload(root), expected_digest
            )
        except ValidationError:
            return True
        return False

    def _check_envelope(self, root, result):
        cover = root.find(f"{{{SII_NS}}}SetDTE/{{{SII_NS}}}Caratula")
        if cover is None:
            result["diagnostics"].append(
                self._error("identity", self.env._("The envelope has no Caratula."))
            )
            return
        identities = [item.get("identity", {}) for item in result["documents"]]
        for identity in identities:
            if normalize_vat(
                cover.findtext(f"{{{SII_NS}}}RutEmisor") or ""
            ) != normalize_vat(identity.get("issuer_vat", "")):
                result["diagnostics"].append(
                    self._error(
                        "identity",
                        self.env._("The envelope issuer does not match its DTEs."),
                    )
                )
            if normalize_vat(
                cover.findtext(f"{{{SII_NS}}}RutReceptor") or ""
            ) != normalize_vat(identity.get("receiver_vat", "")):
                result["diagnostics"].append(
                    self._error(
                        "identity",
                        self.env._("The envelope receiver does not match its DTEs."),
                    )
                )
        expected = Counter(item.get("document_code") for item in identities)
        declared = {}
        for subtotal in cover.findall(f"{{{SII_NS}}}SubTotDTE"):
            code = subtotal.findtext(f"{{{SII_NS}}}TpoDTE")
            count = subtotal.findtext(f"{{{SII_NS}}}NroDTE") or ""
            if code in declared or not count.isdigit():
                result["diagnostics"].append(
                    self._error(
                        "identity", self.env._("The envelope subtotals are invalid.")
                    )
                )
                return
            declared[code] = int(count)
        if dict(expected) != declared:
            result["diagnostics"].append(
                self._error(
                    "identity",
                    self.env._("The envelope subtotals do not match its DTEs."),
                )
            )

    @staticmethod
    def _documents_verified(documents):
        return bool(documents) and all(
            not item["diagnostics"]
            and item["schema_state"] == "valid"
            and item["signature_state"] == "valid"
            and item["ted_state"] == "valid"
            for item in documents
        )

    @staticmethod
    def _error(stage, message):
        return {"stage": stage, "message": message}

    @staticmethod
    def _warning(stage, message):
        return {"stage": stage, "message": message, "severity": "warning"}


def normalize_vat(value):
    return "".join(char for char in value.upper() if char.isalnum())
