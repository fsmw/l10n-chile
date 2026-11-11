# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from lxml import etree

from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_core.tools.xml_safety import safe_fromstring
from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import (
    SII_NAMESPACE,
    DteXmlComposer,
    _add_optional_text,
    _add_text,
    _dte_schema,
    _extract_dd_bytes,
)


class NoteXmlComposer:
    """Serialize a single referenced note using the DTE_v10 document schema."""

    def compose_ted(self, snapshot, caf, signature_service=None):
        """Build the CAF-signed TED using the common DTE_v10 primitive."""
        from odoo.addons.l10n_cl_dte_xml.services.ted_composer import DteTedComposer

        return DteTedComposer().compose_ted(snapshot, caf, signature_service)

    def compose_unsigned_document(self, snapshot):  # noqa: C901
        """Serialize one note and its SII reference before inserting the TED."""
        code = str(snapshot.get("document_code"))
        if code not in {"61", "56"} or len(snapshot.get("lines", [])) > 60:
            raise ValidationError(
                _("Only DTE notes 61/56 with at most 60 details are supported.")
            )
        refs = snapshot.get("references") or []
        if not refs and snapshot.get("reference"):
            ref = snapshot["reference"]
            refs = [
                {
                    "line_number": 1,
                    "document_type": ref.get("document_code"),
                    "folio": ref.get("folio"),
                    "date": ref.get("issue_date"),
                    "reason": ref.get("reason"),
                    "reference_code": ref.get("reference_code"),
                }
            ]
        if not 1 <= len(refs) <= 2 or any(
            any(
                ref.get(key) in (None, "")
                for key in ("document_type", "folio", "date", "reason")
            )
            for ref in refs
        ):
            raise ValidationError(
                _("The DTE note must contain complete fiscal references.")
            )
        nsmap = {None: SII_NAMESPACE}
        if snapshot.get("certification"):
            nsmap["xsi"] = "http://www.w3.org/2001/XMLSchema-instance"
        root = etree.Element(f"{{{SII_NAMESPACE}}}DTE", nsmap=nsmap, version="1.0")
        document = etree.SubElement(
            root,
            f"{{{SII_NAMESPACE}}}Documento",
            ID=f"F{int(snapshot['folio']):010d}T{code}",
        )
        header = etree.SubElement(document, f"{{{SII_NAMESPACE}}}Encabezado")
        identification = etree.SubElement(header, f"{{{SII_NAMESPACE}}}IdDoc")
        for name, value in (
            ("TipoDTE", code),
            ("Folio", snapshot["folio"]),
            ("FchEmis", snapshot["issue_date"]),
        ):
            _add_text(identification, name, value)
        issuer_data = snapshot["issuer"]
        issuer = etree.SubElement(header, f"{{{SII_NAMESPACE}}}Emisor")
        for name in ("vat", "name", "activity", "activity_code"):
            xml_name = {
                "vat": "RUTEmisor",
                "name": "RznSoc",
                "activity": "GiroEmis",
                "activity_code": "Acteco",
            }[name]
            _add_text(issuer, xml_name, issuer_data.get(name))
        for key, xml_name in (
            ("address", "DirOrigen"),
            ("commune", "CmnaOrigen"),
            ("city", "CiudadOrigen"),
        ):
            _add_optional_text(issuer, xml_name, issuer_data.get(key))
        receiver_data = snapshot["receiver"]
        receiver = etree.SubElement(header, f"{{{SII_NAMESPACE}}}Receptor")
        _add_text(receiver, "RUTRecep", receiver_data.get("vat"))
        _add_text(receiver, "RznSocRecep", receiver_data.get("name"))
        for key, xml_name in (
            ("activity", "GiroRecep"),
            ("address", "DirRecep"),
            ("commune", "CmnaRecep"),
            ("city", "CiudadRecep"),
        ):
            _add_optional_text(receiver, xml_name, receiver_data.get(key))
        totals_data = snapshot["totals"]
        totals = etree.SubElement(header, f"{{{SII_NAMESPACE}}}Totales")
        for key, xml_name in (
            ("net", "MntNeto"),
            ("exempt", "MntExe"),
            ("vat_rate", "TasaIVA"),
            ("vat", "IVA"),
            ("total", "MntTotal"),
        ):
            _add_optional_text(totals, xml_name, totals_data.get(key))
        for index, line_data in enumerate(snapshot["lines"], start=1):
            detail = etree.SubElement(document, f"{{{SII_NAMESPACE}}}Detalle")
            _add_text(detail, "NroLinDet", index)
            if line_data.get("exempt"):
                _add_text(detail, "IndExe", "1")
            _add_text(detail, "NmbItem", line_data["name"])
            _add_optional_text(detail, "DscItem", line_data.get("description"))
            if not line_data.get("text_correction"):
                _add_text(detail, "QtyItem", line_data["quantity"])
                _add_optional_text(detail, "UnmdItem", line_data.get("uom"))
                _add_text(detail, "PrcItem", line_data["unit_price"])
                if line_data.get("discount_percent"):
                    _add_text(detail, "DescuentoPct", line_data["discount_percent"])
                    discount = DteXmlComposer.line_discount_amount(
                        line_data["quantity"],
                        line_data["unit_price"],
                        line_data["discount_percent"],
                    )
                    _add_text(detail, "DescuentoMonto", str(discount))
            _add_text(detail, "MontoItem", line_data["subtotal"])
        for index, ref in enumerate(refs, start=1):
            reference = etree.SubElement(document, f"{{{SII_NAMESPACE}}}Referencia")
            for name, value in (
                ("NroLinRef", index),
                ("TpoDocRef", ref["document_type"]),
                ("FolioRef", ref["folio"]),
                ("FchRef", ref["date"]),
            ):
                _add_text(reference, name, value)
            _add_optional_text(reference, "CodRef", ref.get("reference_code"))
            _add_text(reference, "RazonRef", ref["reason"])
        etree.SubElement(document, f"{{{SII_NAMESPACE}}}TED_PLACEHOLDER")
        _add_text(document, "TmstFirma", snapshot["document_timestamp"])
        try:
            xml_bytes = etree.tostring(
                root, encoding="ISO-8859-1", xml_declaration=True, pretty_print=True
            )
        except (LookupError, UnicodeEncodeError) as error:
            raise ValidationError(
                _("The DTE note contains characters outside ISO-8859-1.")
            ) from error
        ted = snapshot.get("ted_xml")
        if not ted or xml_bytes.count(b"<TED_PLACEHOLDER/>") != 1:
            raise ValidationError(
                _("The DTE note must include exactly one signed TED.")
            )
        return xml_bytes.replace(b"<TED_PLACEHOLDER/>", ted, 1)

    def validate_document(self, xml_bytes, signature_service=None):
        """Verify DTE_v10 XSD, one reference, CAF TED and XMLDSig."""
        if not isinstance(xml_bytes, bytes) or b"<!DOCTYPE" in xml_bytes.upper():
            raise ValidationError(_("DTE note XML must be bytes without a DTD."))
        try:
            root = safe_fromstring(xml_bytes)
            _dte_schema().assertValid(root)
        except etree.DocumentInvalid as error:
            raise ValidationError(
                _("The DTE note does not comply with DTE_v10: %s")
                % _dte_schema().error_log.last_error
            ) from error
        except etree.XMLSyntaxError as error:
            raise ValidationError(
                _("The DTE note XML is malformed: %s") % error
            ) from error
        document = root.find(f"{{{SII_NAMESPACE}}}Documento")
        if document is None:
            raise ValidationError(_("The DTE note has no Documento element."))
        code = document.findtext(
            f"{{{SII_NAMESPACE}}}Encabezado/{{{SII_NAMESPACE}}}IdDoc/{{{SII_NAMESPACE}}}TipoDTE"
        )
        references = document.findall(f"{{{SII_NAMESPACE}}}Referencia")
        if code not in {"61", "56"} or len(references) not in {1, 2}:
            raise ValidationError(
                _("A DTE note must be type 61/56 and have one or two references.")
            )
        for index, ref in enumerate(references, start=1):
            required = ("TpoDocRef", "FolioRef", "FchRef", "RazonRef")
            if not all(ref.findtext(f"{{{SII_NAMESPACE}}}{name}") for name in required):
                raise ValidationError(_("The DTE note reference is incomplete."))
            if index == 2 and not ref.findtext(f"{{{SII_NAMESPACE}}}CodRef"):
                raise ValidationError(_("The referenced DTE note requires CodRef."))
        if (
            len(references) == 2
            and references[0].findtext(f"{{{SII_NAMESPACE}}}TpoDocRef") != "SET"
        ):
            raise ValidationError(_("The first certification reference must be SET."))
        ted = self._child_by_local_name(document, "TED")
        dd = self._child_by_local_name(ted, "DD")
        caf = self._child_by_local_name(dd, "CAF")
        frmt_node = self._child_by_local_name(ted, "FRMT")
        frmt = frmt_node.text if frmt_node is not None else None
        if dd is None or caf is None or not frmt:
            raise ValidationError(_("The DTE note TED is missing DD, CAF, or FRMT."))
        signer = signature_service or SignatureService()
        signer.verify_ted(_extract_dd_bytes(xml_bytes), frmt, caf)
        signer.verify_signature(xml_bytes)
        return True

    @staticmethod
    def _child_by_local_name(parent, name):
        if parent is None:
            return None
        return next(
            (child for child in parent if etree.QName(child).localname == name),
            None,
        )
