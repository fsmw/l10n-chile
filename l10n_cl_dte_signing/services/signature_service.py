# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import binascii
import copy
import re

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from lxml import etree

from odoo import _
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.l10n_cl_dte_core.tools.xml_safety import safe_fromstring

from .certificate_identity import certificate_sender_rut

DS_NAMESPACE = "http://www.w3.org/2000/09/xmldsig#"
C14N_URI = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
RSA_SHA1_URI = DS_NAMESPACE + "rsa-sha1"
SHA1_URI = DS_NAMESPACE + "sha1"
SII_XML_DECLARATION = b'<?xml version="1.0" encoding="ISO-8859-1"?>'


class SignatureService:
    """Create and verify the SII's TED FRMT and DTE XMLDSig signatures."""

    def certification_signing_identity(self, company):
        """Return the active Certification certificate and its holder RUT."""
        profile, holder = self.signing_identity(
            company, "certification", manager_only=True
        )
        return profile.sudo().certificate_id, holder

    def signing_identity(self, company, environment, *, manager_only=False):
        """Return a configured signing identity for one explicit SII mode."""
        if environment not in {"certification", "production"}:
            raise ValidationError(_("A real SII environment is required."))
        if not company.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            if (
                manager_only
                or environment == "certification"
                or not company.env.su
                and not company.env.user.has_group("l10n_cl_dte_core.group_dte_user")
            ):
                raise AccessError(
                    _("Only a DTE manager can use the signing certificate.")
                )
        profiles = (
            company.env["l10n_cl.dte.signing.profile"]
            .sudo()
            .search(
                [
                    ("company_id", "=", company.id),
                    ("sii_environment", "=", environment),
                    ("active", "=", True),
                ]
            )
        )
        if len(profiles) != 1 or profiles.is_synthetic:
            raise UserError(
                _(
                    "Configure one non-synthetic signing profile for this "
                    "SII environment."
                )
            )
        certificate = profiles.sudo().certificate_id
        if (
            not certificate
            or certificate.company_id != company
            or not certificate.active
            or not certificate.is_valid
            or not certificate.private_key_id
            or certificate.private_key_id.public
        ):
            raise UserError(
                _(
                    "Configure a valid certificate with its private key for "
                    "this SII environment."
                )
            )
        try:
            pem = base64.b64decode(certificate.pem_certificate, validate=True)
            parsed = x509.load_pem_x509_certificate(pem)
        except (ValueError, TypeError) as error:
            raise ValidationError(
                _("The configured SII certificate is invalid.")
            ) from error
        return profiles, certificate_sender_rut(parsed)

    def sign_ted(self, company, caf_authorization, dd_xml):
        """Sign the serialized TED/DD with the private key embedded in its CAF."""
        caf_authorization.ensure_one()
        if caf_authorization.company_id != company:
            raise ValidationError(_("The CAF belongs to another company."))
        if caf_authorization.state not in {"validated", "exhausted"}:
            raise UserError(_("Only an active CAF can sign a TED."))
        caf_authorization._check_caf_integrity()
        authorization = safe_fromstring(
            caf_authorization._decode_content(caf_authorization.caf_file)
        )
        private_key = _load_rsa_private_key(authorization.findtext("RSASK"))
        public_key = _load_rsa_public_key(authorization.findtext("RSAPUBK"))
        dd_element = _parse_xml(dd_xml)
        if _local_name(dd_element) != "DD":
            raise ValidationError(_("The TED signing input must be the DD node."))
        signing_bytes = _sii_signing_bytes(dd_xml)
        signature = private_key.sign(signing_bytes, padding.PKCS1v15(), hashes.SHA1())
        try:
            public_key.verify(
                signature, signing_bytes, padding.PKCS1v15(), hashes.SHA1()
            )
        except InvalidSignature as error:
            raise ValidationError(
                _("The generated TED signature did not verify with the CAF key pair.")
            ) from error
        return base64.b64encode(signature).decode("ascii")

    def verify_ted(self, dd_xml, frmt_value, caf_element):
        """Verify FRMT using the RSAPK embedded in the document's CAF."""
        dd_element = _parse_xml(dd_xml)
        if _local_name(dd_element) != "DD":
            raise ValidationError(_("The TED signature input must be the DD node."))
        if _local_name(caf_element) != "CAF":
            raise ValidationError(_("The TED must include a CAF element."))
        modulus = _local_path_text(caf_element, ("DA", "RSAPK", "M"))
        exponent = _local_path_text(caf_element, ("DA", "RSAPK", "E"))
        try:
            n = int.from_bytes(base64.b64decode(modulus or "", validate=True), "big")
            e = int.from_bytes(base64.b64decode(exponent or "", validate=True), "big")
            public_key = rsa.RSAPublicNumbers(e, n).public_key()
            signature = base64.b64decode(
                "".join((frmt_value or "").split()), validate=True
            )
        except (binascii.Error, ValueError) as error:
            raise ValidationError(_("TED CAF key or FRMT is malformed.")) from error
        try:
            public_key.verify(
                signature,
                _sii_signing_bytes(dd_xml),
                padding.PKCS1v15(),
                hashes.SHA1(),
            )
        except InvalidSignature as error:
            raise ValidationError(_("The TED FRMT signature is invalid.")) from error
        return True

    def sign_document(self, company, environment, unsigned_xml):
        """Append an enveloped XMLDSig over the DTE's Documento element."""
        # The issuance service checks the operator before entering sudo mode.
        # Cashiers may request issuance without reading private certificate data.
        if not company.env.su and not company.env.user.has_group(
            "l10n_cl_dte_core.group_dte_manager"
        ):
            raise AccessError(_("Only a DTE manager can use the signing certificate."))
        profile = company.env["l10n_cl.dte.signing.profile"].search(
            [
                ("company_id", "=", company.id),
                ("sii_environment", "=", environment),
                ("active", "=", True),
            ],
            limit=1,
        )
        certificate = profile.sudo().certificate_id
        if not certificate or certificate.company_id != company:
            raise UserError(
                _("Configure a signing profile for this company and SII environment.")
            )
        if not certificate.active or not certificate.is_valid:
            raise UserError(_("The configured signing certificate is not valid."))
        if not certificate.private_key_id or certificate.private_key_id.public:
            raise UserError(_("The configured certificate has no private key."))

        root = _parse_xml(unsigned_xml)
        if _local_name(root) != "DTE":
            raise ValidationError(_("The XML root element must be DTE."))
        documents = root.xpath("./*[local-name()='Documento']")
        if len(documents) != 1:
            raise ValidationError(_("A DTE must contain exactly one Documento."))
        document = documents[0]
        document_id = document.get("ID")
        if not document_id or root.xpath(".//*[@ID=$target]", target=document_id) != [
            document
        ]:
            raise ValidationError(_("Documento must have a unique ID attribute."))
        if root.xpath("./*[local-name()='Signature']"):
            raise ValidationError(_("The DTE already contains an XML signature."))
        # SII verifies each DTE independently of the envelope's xsi namespace.
        # Do not sign unused namespace bindings inherited from an old wrapper.
        etree.cleanup_namespaces(root)

        signature = etree.SubElement(
            root, _qname("Signature"), nsmap={None: DS_NAMESPACE}
        )
        signed_info = etree.SubElement(signature, _qname("SignedInfo"))
        etree.SubElement(
            signed_info, _qname("CanonicalizationMethod"), Algorithm=C14N_URI
        )
        etree.SubElement(signed_info, _qname("SignatureMethod"), Algorithm=RSA_SHA1_URI)
        reference = etree.SubElement(
            signed_info, _qname("Reference"), URI=f"#{document_id}"
        )
        etree.SubElement(reference, _qname("DigestMethod"), Algorithm=SHA1_URI)
        digest = hashes.Hash(hashes.SHA1())
        digest.update(_canonicalize(document))
        etree.SubElement(reference, _qname("DigestValue")).text = base64.b64encode(
            digest.finalize()
        ).decode("ascii")

        signature_bytes = certificate._sign(
            _canonicalize(signed_info), hashing_algorithm="sha1", formatting="base64"
        )
        etree.SubElement(
            signature, _qname("SignatureValue")
        ).text = signature_bytes.decode("ascii")
        self._add_key_info(signature, certificate)
        signed_xml = etree.tostring(
            root,
            encoding="ISO-8859-1",
            xml_declaration=True,
            pretty_print=False,
        )
        self.verify_signature(signed_xml, sii_standalone=True)
        return signed_xml

    def verify_signature(self, signed_xml, public_key=None, *, sii_standalone=False):
        """Verify one SII DTE XMLDSig and its digest; raise on any mismatch."""
        root = _parse_xml(signed_xml)
        if sii_standalone:
            etree.cleanup_namespaces(root)
        signatures = root.xpath("./*[local-name()='Signature']")
        documents = root.xpath("./*[local-name()='Documento']")
        if len(signatures) != 1 or len(documents) != 1:
            raise ValidationError(_("A signed DTE needs one Documento and Signature."))
        signature = signatures[0]
        document = documents[0]
        signed_info = signature.find(_qname("SignedInfo"))
        signature_value = signature.findtext(_qname("SignatureValue"))
        if signed_info is None or not signature_value:
            raise ValidationError(_("The XML signature is incomplete."))
        canonicalization = signed_info.find(_qname("CanonicalizationMethod"))
        signature_method = signed_info.find(_qname("SignatureMethod"))
        reference = signed_info.find(_qname("Reference"))
        if (
            canonicalization is None
            or canonicalization.get("Algorithm") != C14N_URI
            or signature_method is None
            or signature_method.get("Algorithm") != RSA_SHA1_URI
            or reference is None
        ):
            raise ValidationError(_("The XML signature algorithms are unsupported."))
        document_id = document.get("ID")
        if not document_id or reference.get("URI") != f"#{document_id}":
            raise ValidationError(_("The XML signature does not reference Documento."))
        if root.xpath(".//*[@ID=$target]", target=document_id) != [document]:
            raise ValidationError(_("Documento ID must be unique in the DTE."))
        digest_method = reference.find(_qname("DigestMethod"))
        digest_value = reference.findtext(_qname("DigestValue"))
        if digest_method is None or digest_method.get("Algorithm") != SHA1_URI:
            raise ValidationError(_("The XML digest algorithm is unsupported."))
        try:
            expected_digest = base64.b64decode(
                "".join((digest_value or "").split()), validate=True
            )
            signature_bytes = base64.b64decode(
                "".join(signature_value.split()), validate=True
            )
        except (binascii.Error, ValueError) as error:
            raise ValidationError(
                _("The XML signature contains invalid Base64.")
            ) from error
        digest_variant = self._verify_document_digest(
            document, expected_digest, allow_legacy=not sii_standalone
        )

        key = public_key or self._public_key_from_key_info(signature)
        if not isinstance(key, rsa.RSAPublicKey):
            raise ValidationError(_("Only RSA XMLDSig public keys are supported."))
        signed_info_variant = self._verify_signed_info(
            key, signature_bytes, signed_info, allow_legacy=not sii_standalone
        )
        if not signed_info_variant:
            raise ValidationError(_("The XML signature is invalid."))
        return {
            "valid": True,
            "document_digest_canonicalization": digest_variant,
            "signed_info_canonicalization": signed_info_variant,
        }

    def sign_authentication_seed(self, company, environment, seed):
        """Sign SII's enveloped getToken request without persisting credentials."""
        self.env = company.env
        if (
            environment not in {"certification", "production"}
            or not str(seed).isdigit()
        ):
            raise ValidationError(
                _(
                    "A numeric SII authentication seed and real environment "
                    "are required."
                )
            )
        profile, _holder = self.signing_identity(company, environment)
        certificate = profile.sudo().certificate_id
        root = etree.Element("getToken")
        etree.SubElement(etree.SubElement(root, "item"), "Semilla").text = str(seed)
        digest = hashes.Hash(hashes.SHA1())
        digest.update(_canonicalize(root))
        signature = etree.SubElement(
            root, _qname("Signature"), nsmap={None: DS_NAMESPACE}
        )
        info = etree.SubElement(signature, _qname("SignedInfo"))
        etree.SubElement(info, _qname("CanonicalizationMethod"), Algorithm=C14N_URI)
        etree.SubElement(info, _qname("SignatureMethod"), Algorithm=RSA_SHA1_URI)
        reference = etree.SubElement(info, _qname("Reference"), URI="")
        transforms = etree.SubElement(reference, _qname("Transforms"))
        etree.SubElement(
            transforms,
            _qname("Transform"),
            Algorithm=DS_NAMESPACE + "enveloped-signature",
        )
        etree.SubElement(reference, _qname("DigestMethod"), Algorithm=SHA1_URI)
        etree.SubElement(reference, _qname("DigestValue")).text = base64.b64encode(
            digest.finalize()
        ).decode("ascii")
        value = certificate._sign(
            _canonicalize(info), hashing_algorithm="sha1", formatting="base64"
        )
        etree.SubElement(signature, _qname("SignatureValue")).text = value.decode(
            "ascii"
        )
        self._add_key_info(signature, certificate)
        return etree.tostring(root, encoding="ISO-8859-1", xml_declaration=True)

    def sign_envelope(self, company, environment, unsigned_xml):
        """Sign the SetDTE inside an already assembled SII EnvioDTE."""
        if not company.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(_("Only a DTE manager can use the signing certificate."))
        profiles = company.env["l10n_cl.dte.signing.profile"].search(
            [
                ("company_id", "=", company.id),
                ("sii_environment", "=", environment),
                ("active", "=", True),
            ]
        )
        if len(profiles) != 1:
            raise UserError(
                _("Select exactly one active signing profile for this environment.")
            )
        certificate = profiles.sudo().certificate_id
        if (
            not certificate
            or certificate.company_id != company
            or not certificate.active
            or not certificate.is_valid
        ):
            raise UserError(_("The certification signing certificate is not valid."))
        if not certificate.private_key_id or certificate.private_key_id.public:
            raise UserError(_("The signing certificate has no private key."))
        root = _parse_xml(unsigned_xml)
        if (
            _local_name(root) != "EnvioDTE"
            or len(root.xpath("./*[local-name()='SetDTE']")) != 1
        ):
            raise ValidationError(_("The envelope must have one SetDTE."))
        target = root.xpath("./*[local-name()='SetDTE']")[0]
        target_id = target.get("ID")
        if not target_id or root.xpath(".//*[@ID=$target]", target=target_id) != [
            target
        ]:
            raise ValidationError(_("SetDTE needs a unique ID."))
        if root.xpath("./*[local-name()='Signature']"):
            raise ValidationError(_("The envelope is already signed."))
        signature = etree.SubElement(
            root, _qname("Signature"), nsmap={None: DS_NAMESPACE}
        )
        signed_info = etree.SubElement(signature, _qname("SignedInfo"))
        etree.SubElement(
            signed_info, _qname("CanonicalizationMethod"), Algorithm=C14N_URI
        )
        etree.SubElement(signed_info, _qname("SignatureMethod"), Algorithm=RSA_SHA1_URI)
        reference = etree.SubElement(
            signed_info, _qname("Reference"), URI=f"#{target_id}"
        )
        etree.SubElement(reference, _qname("DigestMethod"), Algorithm=SHA1_URI)
        digest = hashes.Hash(hashes.SHA1())
        digest.update(_canonicalize(target))
        etree.SubElement(reference, _qname("DigestValue")).text = base64.b64encode(
            digest.finalize()
        ).decode("ascii")
        signature_bytes = certificate._sign(
            _canonicalize(signed_info), hashing_algorithm="sha1", formatting="base64"
        )
        etree.SubElement(
            signature, _qname("SignatureValue")
        ).text = signature_bytes.decode("ascii")
        self._add_key_info(signature, certificate)
        # Preserve every inner signed DTE byte. Re-serializing the wrapper can
        # change their independent namespace context at the SII receiver.
        body = _without_xml_declaration(unsigned_xml)
        opening_end = body.index(b">") + 1
        if not body[opening_end:].startswith(b"\n"):
            body = body[:opening_end] + b"\n" + body[opening_end:]
        before, closing, after = body.rpartition(b"</EnvioDTE>")
        if not closing or after.strip():
            raise ValidationError(_("The XML root element must be EnvioDTE."))
        envelope = (
            SII_XML_DECLARATION
            + b"\n"
            + before
            + b"\n"
            + etree.tostring(
                signature,
                encoding="ISO-8859-1",
                xml_declaration=False,
                with_tail=False,
            )
            + b"\n"
            + closing
            + after
        )
        self.verify_envelope_signature(envelope)
        return envelope

    def verify_envelope_signature(self, envelope_bytes):
        """Verify only the outer SetDTE reference; inner DTEs are separate."""
        root = _parse_xml(envelope_bytes)
        if _local_name(root) not in {"EnvioDTE", "EnvioBOLETA"}:
            raise ValidationError(_("The XML root must be EnvioDTE or EnvioBOLETA."))
        sets = root.xpath("./*[local-name()='SetDTE']")
        signatures = root.xpath("./*[local-name()='Signature']")
        if len(sets) != 1 or len(signatures) != 1:
            raise ValidationError(_("An envelope needs one SetDTE and one signature."))
        target, signature = sets[0], signatures[0]
        signed_info = signature.find(_qname("SignedInfo"))
        signature_value = signature.findtext(_qname("SignatureValue"))
        if signed_info is None or not signature_value:
            raise ValidationError(_("The envelope signature is incomplete."))
        canonicalization = signed_info.find(_qname("CanonicalizationMethod"))
        signature_method = signed_info.find(_qname("SignatureMethod"))
        references = signed_info.findall(_qname("Reference"))
        if (
            canonicalization is None
            or canonicalization.get("Algorithm") != C14N_URI
            or signature_method is None
            or signature_method.get("Algorithm") != RSA_SHA1_URI
            or len(references) != 1
        ):
            raise ValidationError(
                _("The envelope signature algorithms are unsupported.")
            )
        reference = references[0]
        target_id = target.get("ID")
        if (
            not target_id
            or reference.get("URI") != f"#{target_id}"
            or root.xpath(".//*[@ID=$target]", target=target_id) != [target]
        ):
            raise ValidationError(
                _("The envelope signature does not reference its unique SetDTE.")
            )
        digest_method = reference.find(_qname("DigestMethod"))
        if digest_method is None or digest_method.get("Algorithm") != SHA1_URI:
            raise ValidationError(_("The envelope digest algorithm is unsupported."))
        try:
            expected = base64.b64decode(
                "".join((reference.findtext(_qname("DigestValue")) or "").split()),
                validate=True,
            )
            signed = base64.b64decode("".join(signature_value.split()), validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValidationError(
                _("The envelope signature has invalid Base64.")
            ) from error
        self._verify_document_digest(target, expected)
        key = self._public_key_from_key_info(signature)
        if not isinstance(key, rsa.RSAPublicKey) or not self._verify_signed_info(
            key, signed, signed_info
        ):
            raise ValidationError(_("The envelope XML signature is invalid."))
        return True

    def sign_certification_book(self, company, environment, unsigned_xml):
        """Sign EnvioLibro using the certificate selected for its SII mode."""
        if not company.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(_("Only a DTE manager can use the signing certificate."))
        profile = company.env["l10n_cl.dte.signing.profile"].search(
            [
                ("company_id", "=", company.id),
                ("sii_environment", "=", environment),
                ("active", "=", True),
            ],
            limit=1,
        )
        certificate = profile.sudo().certificate_id
        if (
            not certificate
            or certificate.company_id != company
            or not certificate.active
            or not certificate.is_valid
            or not certificate.private_key_id
            or certificate.private_key_id.public
        ):
            raise UserError(
                _("Configure a valid private signing certificate for this environment.")
            )
        root = _parse_xml(unsigned_xml)
        if _local_name(root) != "LibroCompraVenta":
            raise ValidationError(
                _("The electronic book root element must be LibroCompraVenta.")
            )
        books = root.xpath("./*[local-name()='EnvioLibro']")
        if len(books) != 1 or root.xpath("./*[local-name()='Signature']"):
            raise ValidationError(
                _("An electronic book must contain one unsigned EnvioLibro.")
            )
        target = books[0]
        target_id = target.get("ID")
        if not target_id or root.xpath(".//*[@ID=$target]", target=target_id) != [
            target
        ]:
            raise ValidationError(_("EnvioLibro must have a unique ID."))
        signature = etree.SubElement(
            root, _qname("Signature"), nsmap={None: DS_NAMESPACE}
        )
        signed_info = etree.SubElement(signature, _qname("SignedInfo"))
        etree.SubElement(
            signed_info, _qname("CanonicalizationMethod"), Algorithm=C14N_URI
        )
        etree.SubElement(signed_info, _qname("SignatureMethod"), Algorithm=RSA_SHA1_URI)
        reference = etree.SubElement(
            signed_info, _qname("Reference"), URI=f"#{target_id}"
        )
        etree.SubElement(reference, _qname("DigestMethod"), Algorithm=SHA1_URI)
        digest = hashes.Hash(hashes.SHA1())
        digest.update(_canonicalize(target))
        etree.SubElement(reference, _qname("DigestValue")).text = base64.b64encode(
            digest.finalize()
        ).decode("ascii")
        signature_bytes = certificate._sign(
            _canonicalize(signed_info), hashing_algorithm="sha1", formatting="base64"
        )
        etree.SubElement(
            signature, _qname("SignatureValue")
        ).text = signature_bytes.decode("ascii")
        self._add_key_info(signature, certificate)
        signed_xml = etree.tostring(
            root, encoding="ISO-8859-1", xml_declaration=True, pretty_print=False
        )
        self.verify_certification_book_signature(signed_xml)
        return signed_xml

    def verify_certification_book_signature(self, book_xml):
        """Verify the XMLDSig reference over the unique EnvioLibro element."""
        root = _parse_xml(book_xml)
        if _local_name(root) != "LibroCompraVenta":
            raise ValidationError(
                _("The electronic book root element must be LibroCompraVenta.")
            )
        books = root.xpath("./*[local-name()='EnvioLibro']")
        signatures = root.xpath("./*[local-name()='Signature']")
        if len(books) != 1 or len(signatures) != 1:
            raise ValidationError(
                _("A signed electronic book needs one EnvioLibro and one Signature.")
            )
        target, signature = books[0], signatures[0]
        signed_info = signature.find(_qname("SignedInfo"))
        signature_value = signature.findtext(_qname("SignatureValue"))
        if signed_info is None or not signature_value:
            raise ValidationError(_("The electronic book signature is incomplete."))
        canonicalization = signed_info.find(_qname("CanonicalizationMethod"))
        signature_method = signed_info.find(_qname("SignatureMethod"))
        references = signed_info.findall(_qname("Reference"))
        target_id = target.get("ID")
        if (
            canonicalization is None
            or canonicalization.get("Algorithm") != C14N_URI
            or signature_method is None
            or signature_method.get("Algorithm") != RSA_SHA1_URI
            or len(references) != 1
            or not target_id
            or references[0].get("URI") != f"#{target_id}"
            or root.xpath(".//*[@ID=$target]", target=target_id) != [target]
        ):
            raise ValidationError(
                _(
                    "The electronic book signature does not reference its unique "
                    "EnvioLibro."
                )
            )
        reference = references[0]
        digest_method = reference.find(_qname("DigestMethod"))
        if digest_method is None or digest_method.get("Algorithm") != SHA1_URI:
            raise ValidationError(
                _("The electronic book digest algorithm is unsupported.")
            )
        try:
            expected_digest = base64.b64decode(
                reference.findtext(_qname("DigestValue")) or "", validate=True
            )
            signature_bytes = base64.b64decode(signature_value, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValidationError(
                _("The electronic book signature contains invalid Base64.")
            ) from error
        self._verify_document_digest(target, expected_digest)
        public_key = self._public_key_from_key_info(signature)
        if not isinstance(public_key, rsa.RSAPublicKey) or not self._verify_signed_info(
            public_key, signature_bytes, signed_info
        ):
            raise ValidationError(_("The electronic book XML signature is invalid."))
        return True

    def _verify_document_digest(self, document, expected_digest, *, allow_legacy=True):
        candidates = (
            ("inclusive", _canonicalize(document)),
            ("exclusive", _canonicalize(document, exclusive=True)),
            (
                "exclusive_without_sii_namespace",
                _canonicalize_without_sii_namespace(document),
            ),
        )
        for variant, payload in candidates if allow_legacy else candidates[:1]:
            digest = hashes.Hash(hashes.SHA1())
            digest.update(payload)
            if digest.finalize() == expected_digest:
                return variant
        raise ValidationError(_("The Documento digest does not match."))

    def _verify_signed_info(
        self, public_key, signature_bytes, signed_info, *, allow_legacy=True
    ):
        candidates = (
            ("inclusive", _canonicalize(signed_info)),
            ("exclusive", _canonicalize(signed_info, exclusive=True)),
        )
        for variant, payload in candidates if allow_legacy else candidates[:1]:
            try:
                public_key.verify(
                    signature_bytes,
                    payload,
                    padding.PKCS1v15(),
                    hashes.SHA1(),
                )
                return variant
            except InvalidSignature:
                continue
        return None

    def _add_key_info(self, signature, certificate):
        key_info = etree.SubElement(signature, _qname("KeyInfo"))
        key_value = etree.SubElement(key_info, _qname("KeyValue"))
        rsa_key_value = etree.SubElement(key_value, _qname("RSAKeyValue"))
        exponent, modulus = certificate._get_public_key_numbers_bytes(
            formatting="base64"
        )
        etree.SubElement(rsa_key_value, _qname("Modulus")).text = modulus.decode(
            "ascii"
        )
        etree.SubElement(rsa_key_value, _qname("Exponent")).text = exponent.decode(
            "ascii"
        )
        pem_certificate = base64.b64decode(certificate.pem_certificate)
        certificate_object = x509.load_pem_x509_certificate(pem_certificate)
        der_certificate = certificate_object.public_bytes(serialization.Encoding.DER)
        x509_data = etree.SubElement(key_info, _qname("X509Data"))
        encoded_certificate = (
            base64.encodebytes(der_certificate).decode("ascii").strip()
        )
        etree.SubElement(
            x509_data, _qname("X509Certificate")
        ).text = encoded_certificate

    def _public_key_from_key_info(self, signature):
        certificates = signature.xpath(
            "./ds:KeyInfo/ds:X509Data/ds:X509Certificate",
            namespaces={"ds": DS_NAMESPACE},
        )
        if certificates:
            try:
                der_certificate = base64.b64decode(
                    "".join((certificates[0].text or "").split()), validate=True
                )
                return x509.load_der_x509_certificate(der_certificate).public_key()
            except (binascii.Error, ValueError) as error:
                raise ValidationError(
                    _("The XML certificate is not valid DER/Base64.")
                ) from error
        modulus = signature.findtext(
            f"{_qname('KeyInfo')}/{_qname('KeyValue')}/"
            f"{_qname('RSAKeyValue')}/{_qname('Modulus')}"
        )
        exponent = signature.findtext(
            f"{_qname('KeyInfo')}/{_qname('KeyValue')}/"
            f"{_qname('RSAKeyValue')}/{_qname('Exponent')}"
        )
        try:
            n = int.from_bytes(base64.b64decode(modulus or "", validate=True), "big")
            e = int.from_bytes(base64.b64decode(exponent or "", validate=True), "big")
            return rsa.RSAPublicNumbers(e, n).public_key()
        except (binascii.Error, ValueError) as error:
            raise ValidationError(_("The XML RSA public key is invalid.")) from error


def _parse_xml(xml_bytes):
    if isinstance(xml_bytes, str):
        xml_bytes = xml_bytes.encode("ISO-8859-1")
    if not isinstance(xml_bytes, bytes) or b"<!DOCTYPE" in xml_bytes.upper():
        raise ValidationError(_("The XML input must be bytes without a DTD."))
    has_declaration = xml_bytes.lstrip().startswith(b"<?xml")
    try:
        return safe_fromstring(
            xml_bytes, encoding=None if has_declaration else "ISO-8859-1"
        )
    except (etree.XMLSyntaxError, ValueError) as error:
        raise ValidationError(
            _("The XML input is not well-formed: %s") % error
        ) from error


def _without_xml_declaration(xml_bytes):
    return re.sub(rb"^\s*<\?xml[^?]*\?>\s*", b"", xml_bytes, count=1).lstrip()


def _canonicalize(element, exclusive=False):
    try:
        # Direct C14N of a descendant can reset inherited default namespaces
        # in nested nodes. Materialize every in-scope namespace at the subtree
        # apex before canonicalizing, matching standalone XMLDSig processors.
        standalone = safe_fromstring(
            etree.tostring(element, encoding="UTF-8", with_tail=False)
        )
        if not exclusive:
            # Inclusive C14N 1.0 also inherits xml:* attributes from omitted
            # ancestors. Preserve the nearest value without replacing local ones.
            for ancestor in element.iterancestors():
                for name, value in ancestor.attrib.items():
                    if (
                        name.startswith("{http://www.w3.org/XML/1998/namespace}")
                        and name not in standalone.attrib
                    ):
                        standalone.set(name, value)
        return etree.tostring(
            standalone, method="c14n", exclusive=exclusive, with_comments=False
        )
    except (etree.C14NError, etree.XMLSyntaxError, ValueError) as error:
        raise ValidationError(_("The XML cannot be canonicalized.")) from error


def _canonicalize_without_sii_namespace(element):
    unqualified = copy.deepcopy(element)
    for node in unqualified.iter():
        qualified_name = etree.QName(node)
        if qualified_name.namespace == "http://www.sii.cl/SiiDte":
            node.tag = qualified_name.localname
    return _canonicalize(unqualified, exclusive=True)


def _sii_signing_bytes(element):
    if isinstance(element, bytes):
        serialized = element
    else:
        serialized = etree.tostring(element, encoding="ISO-8859-1", with_tail=False)
    return re.sub(rb"\n\s*", b"", serialized)


def _load_rsa_private_key(pem):
    if not pem:
        raise ValidationError(_("The CAF does not contain an RSA private key."))
    try:
        key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
    except (UnicodeEncodeError, TypeError, ValueError) as error:
        raise ValidationError(_("The CAF private key is not valid PEM.")) from error
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValidationError(_("The CAF private key must be RSA."))
    return key


def _load_rsa_public_key(pem):
    if not pem:
        raise ValidationError(_("The CAF does not contain an RSA public key."))
    try:
        key = serialization.load_pem_public_key(pem.encode("ascii"))
    except (UnicodeEncodeError, TypeError, ValueError) as error:
        raise ValidationError(_("The CAF public key is not valid PEM.")) from error
    if not isinstance(key, rsa.RSAPublicKey):
        raise ValidationError(_("The CAF public key must be RSA."))
    return key


def _local_name(element):
    return etree.QName(element).localname


def _local_path_text(element, path):
    current = element
    for name in path:
        matches = [child for child in current if _local_name(child) == name]
        if len(matches) != 1:
            return None
        current = matches[0]
    return current.text


def _qname(local_name):
    return f"{{{DS_NAMESPACE}}}{local_name}"
