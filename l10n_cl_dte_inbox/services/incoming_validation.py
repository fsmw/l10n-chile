# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
from collections import Counter

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from lxml import etree
from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import _extract_dd_bytes
from odoo.exceptions import ValidationError
from odoo.tools.misc import file_path

from .incoming_parser import SII_NS, extract_incoming_dtes, parse_incoming_dte

DS_NS = "http://www.w3.org/2000/09/xmldsig#"


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
            try:
                if not self._check_signature_shape(root, result["diagnostics"]):
                    raise ValidationError(
                        self.env._("The envelope signature structure is unsupported.")
                    )
                self.signer.verify_envelope_signature(content)
                result["signature_state"] = "valid"
            except ValidationError as error:
                result["signature_state"] = "invalid"
                result["diagnostics"].append(self._error("signature", str(error)))
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
        try:
            if not self._check_signature_shape(node, result["diagnostics"]):
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
            result["diagnostics"].append(self._error("signature", str(error)))
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
        parser = etree.XMLParser(
            resolve_entities=False, no_network=True, load_dtd=False
        )
        if path not in self.schemas:
            try:
                self.schemas[path] = etree.XMLSchema(etree.parse(path, parser))
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
            if len(transforms) > 1 or any(
                item.get("Algorithm")
                != "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
                for item in transforms
            ):
                diagnostics.append(
                    self._error(
                        "signature",
                        self.env._("The XML signature transform is unsupported."),
                    )
                )
        return len(diagnostics) == previous_count

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
    def _error(stage, message):
        return {"stage": stage, "message": message}


def normalize_vat(value):
    return "".join(char for char in value.upper() if char.isalnum())
