# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
import re
from urllib.parse import urljoin

import requests
from lxml import etree
from zeep import Client
from zeep.exceptions import Error as ZeepError
from zeep.transports import Transport

from .dte_xml import normalize_rut

TIMEOUT = 30
REST_TIMEOUT = 10
SOAP_URLS = {
    "SIITEST": "https://maullin.sii.cl/DTEWS/",
    "SII": "https://palena.sii.cl/DTEWS/",
}
BOLETA_URLS = {
    "SIITEST": {
        "send": "https://pangal.sii.cl/recursos/v1/boleta.electronica.envio",
        "auth": "https://apicert.sii.cl/recursos/v1/",
    },
    "SII": {
        "send": "https://rahue.sii.cl/recursos/v1/boleta.electronica.envio",
        "auth": "https://api.sii.cl/recursos/v1/",
    },
}
REST_STATUSES = {
    "DOK": "accepted",
    "RPR": "objected",
    "RLV": "objected",
    "RCH": "rejected",
}


class SiiTransportError(Exception):
    def __init__(
        self, message, outcome_unknown=False, track_id=None, response_metadata=None
    ):
        super().__init__(message)
        self.outcome_unknown = outcome_unknown
        self.track_id = track_id
        self.response_metadata = response_metadata or {}


class SiiDteClient:
    """SII SOAP transport for invoices and REST transport for boletas."""

    def __init__(self, company, certificate, *, session=None, soap_factory=None):
        self.company = company
        self.certificate = certificate
        self.session = session or requests.Session()
        self.soap_factory = soap_factory or self._new_soap_client
        self.mode = company.l10n_cl_dte_environment
        if self.mode not in SOAP_URLS:
            raise SiiTransportError("SIIDEMO does not permit network transport.")

    @staticmethod
    def _new_soap_client(wsdl):
        session = requests.Session()
        transport = Transport(
            session=session, timeout=TIMEOUT, operation_timeout=TIMEOUT
        )
        return Client(wsdl=wsdl, transport=transport)

    def send(self, xml_payload, filename, document_code):
        try:
            if document_code in {"39", "41"}:
                return self._send_boleta(xml_payload, filename)
            return self._send_invoice(xml_payload, filename)
        except SiiTransportError:
            raise
        except (requests.RequestException, ZeepError) as error:
            raise SiiTransportError(
                "Could not authenticate or submit the DTE to the SII."
            ) from error

    def check_status(self, track_id, document_code=None, folio=None):
        try:
            if document_code in {"39", "41"}:
                return self._check_boleta_status(track_id, document_code, folio)
            return self._check_invoice_status(track_id, document_code)
        except SiiTransportError:
            raise
        except (requests.RequestException, ZeepError) as error:
            raise SiiTransportError("Could not query the SII DTE status.") from error

    def authenticated_soap_service(self, wsdl_name):
        """Return a SOAP service and fresh SII token for an additional query.

        Optional vertical addons use this entry point to share the core
        certificate authentication and environment selection. ``wsdl_name``
        is a service filename, never a caller-supplied URL.
        """
        if (
            not isinstance(wsdl_name, str)
            or "/" in wsdl_name
            or "\\" in wsdl_name
            or not wsdl_name.endswith("?WSDL")
        ):
            raise SiiTransportError("Invalid SII SOAP service name.")
        try:
            token = self._get_token(refresh=True)
            service = self.soap_factory(SOAP_URLS[self.mode] + wsdl_name).service
        except SiiTransportError:
            raise
        except (requests.RequestException, ZeepError) as error:
            raise SiiTransportError(
                "Could not authenticate or create the SII SOAP service."
            ) from error
        return service, token

    def get_auth_token(self, *, refresh=False, rest=False):
        """Return an authenticated token for an additional SII service.

        Optional vertical addons can use the core certificate/token flow
        without reaching into this client's private implementation. Set
        ``rest=True`` for the separate Boleta REST seed/token endpoints. The
        caller remains responsible for choosing a fixed, environment-specific
        SII endpoint.
        """
        try:
            return self._get_token(refresh=refresh, rest=rest)
        except SiiTransportError:
            raise
        except (requests.RequestException, ZeepError) as error:
            raise SiiTransportError(
                "Could not authenticate with the SII service."
            ) from error

    def _fetch_seed(self, rest=False):
        if rest:
            url = urljoin(BOLETA_URLS[self.mode]["auth"], "boleta.electronica.semilla")
            response = self.session.get(url, timeout=REST_TIMEOUT)
            response.raise_for_status()
            root = etree.fromstring(response.content)
        else:
            wsdl = SOAP_URLS[self.mode] + "CrSeed.jws?WSDL"
            response = self.soap_factory(wsdl).service.getSeed()
            root = _parse_xml(response)
        seed = root.xpath("//*[local-name()='SEMILLA']/text()")
        if not seed:
            raise SiiTransportError("The SII did not return an authentication seed.")
        return seed[0]

    def _signed_token(self, seed):
        root = etree.Element("getToken")
        item = etree.SubElement(root, "item")
        etree.SubElement(item, "Semilla").text = seed
        digest = base64.b64encode(hashlib.sha1(_c14n(root)).digest())
        ds = "http://www.w3.org/2000/09/xmldsig#"
        signed_info = etree.SubElement(
            etree.SubElement(root, f"{{{ds}}}Signature", nsmap={None: ds}),
            f"{{{ds}}}SignedInfo",
        )
        etree.SubElement(
            signed_info,
            f"{{{ds}}}CanonicalizationMethod",
            Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315",
        )
        etree.SubElement(
            signed_info,
            f"{{{ds}}}SignatureMethod",
            Algorithm=f"{ds}rsa-sha1",
        )
        reference = etree.SubElement(signed_info, f"{{{ds}}}Reference", URI="")
        transforms = etree.SubElement(reference, f"{{{ds}}}Transforms")
        etree.SubElement(
            transforms,
            f"{{{ds}}}Transform",
            Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315",
        )
        etree.SubElement(reference, f"{{{ds}}}DigestMethod", Algorithm=f"{ds}sha1")
        etree.SubElement(reference, f"{{{ds}}}DigestValue").text = digest.decode()
        signature_value = self.certificate._sign(
            _c14n(signed_info), hashing_algorithm="sha1", formatting="base64"
        )
        parent = signed_info.getparent()
        value = etree.SubElement(parent, f"{{{ds}}}SignatureValue")
        value.text = _ascii(signature_value)
        modulus, exponent = self.certificate._get_public_key_numbers_bytes(
            formatting="base64"
        )
        key_info = etree.SubElement(parent, f"{{{ds}}}KeyInfo")
        rsa_value = etree.SubElement(
            etree.SubElement(key_info, f"{{{ds}}}KeyValue"),
            f"{{{ds}}}RSAKeyValue",
        )
        etree.SubElement(rsa_value, f"{{{ds}}}Modulus").text = _ascii(modulus)
        etree.SubElement(rsa_value, f"{{{ds}}}Exponent").text = _ascii(exponent)
        x509 = etree.SubElement(key_info, f"{{{ds}}}X509Data")
        etree.SubElement(x509, f"{{{ds}}}X509Certificate").text = _ascii(
            self.certificate._get_der_certificate_bytes(formatting="base64")
        )
        return etree.tostring(root, encoding="ISO-8859-1", xml_declaration=True)

    def _get_token(self, rest=False, refresh=False):
        field_name = "last_rest_token" if rest else "last_token"
        certificate = self.certificate.sudo()
        cached = certificate[field_name]
        if cached and not refresh:
            return cached
        seed = self._fetch_seed(rest=rest)
        signed = self._signed_token(seed)
        if rest:
            url = urljoin(BOLETA_URLS[self.mode]["auth"], "boleta.electronica.token")
            response = self.session.post(
                url,
                headers={
                    "Accept": "application/xml",
                    "Content-Type": "application/xml",
                },
                data=signed,
                timeout=REST_TIMEOUT,
            )
            response.raise_for_status()
            root = etree.fromstring(response.content)
        else:
            wsdl = SOAP_URLS[self.mode] + "GetTokenFromSeed.jws?WSDL"
            response = self.soap_factory(wsdl).service.getToken(
                signed.decode("ISO-8859-1")
            )
            root = _parse_xml(response)
        token = root.xpath("//*[local-name()='TOKEN']/text()")
        if not token:
            message = root.xpath("//*[local-name()='GLOSA']/text()")
            raise SiiTransportError(
                message[0]
                if message
                else "The SII did not return an authentication token."
            )
        certificate.write({field_name: token[0]})
        return token[0]

    def _sender_data(self):
        sender_rut, sender_dv = _rut_parts(self.certificate.subject_serial_number)
        company_rut, company_dv = _rut_parts(self.company.vat)
        return sender_rut, sender_dv, company_rut, company_dv

    def _send_invoice(self, xml_payload, filename):
        sender_rut, sender_dv, company_rut, company_dv = self._sender_data()
        data = {
            "rutSender": sender_rut,
            "dvSender": sender_dv,
            "rutCompany": company_rut,
            "dvCompany": company_dv,
        }
        files = {"archivo": (filename, xml_payload, "text/xml")}
        url = SOAP_URLS[self.mode].replace("/DTEWS/", "/cgi_dte/UPL/DTEUpload")
        for attempt in range(2):
            token = self._get_token(refresh=bool(attempt))
            headers = {
                "Cookie": f"TOKEN={token}",
                "User-Agent": "Mozilla/4.0 (compatible; PROG 1.0; Windows NT)",
                "Referer": self.company.website or "https://www.sii.cl/",
            }
            try:
                response = self.session.post(
                    url, data=data, files=files, headers=headers, timeout=TIMEOUT
                )
            except requests.RequestException as error:
                raise SiiTransportError(
                    "The SII connection ended without confirming receipt.",
                    outcome_unknown=True,
                ) from error
            if response.status_code == 401 and attempt == 0:
                continue
            if response.status_code >= 500:
                raise SiiTransportError(
                    "The SII did not confirm document receipt.", outcome_unknown=True
                )
            try:
                response.raise_for_status()
            except requests.HTTPError as error:
                raise SiiTransportError(
                    "The SII rejected the upload request."
                ) from error
            root = etree.HTML(response.content)
            statuses = _html_values(root, "STATUS")
            track_ids = _html_values(root, "TRACKID")
            if len(statuses) != 1:
                raise SiiTransportError(
                    "The SII response has no unique STATUS; "
                    "receipt could not be confirmed.",
                    outcome_unknown=True,
                )
            reception_status = statuses[0].strip()
            if reception_status == "5":
                if attempt == 0:
                    continue
                raise SiiTransportError("The SII rejected the authentication token.")
            if not re.fullmatch(r"\d+", reception_status):
                raise SiiTransportError(
                    "The SII returned an unrecognized upload STATUS; "
                    "receipt is uncertain.",
                    outcome_unknown=True,
                )
            if reception_status != "0":
                diagnostic = _html_text(root, "GLOSA") or _html_text(root, "GLOSA_ERR")
                return {
                    "track_id": track_ids[0].strip() if len(track_ids) == 1 else False,
                    "status": "rejected",
                    "diagnostic": diagnostic or f"SII upload STATUS={reception_status}",
                }
            if len(track_ids) != 1 or not track_ids[0].strip():
                raise SiiTransportError(
                    "The SII returned STATUS=0 without one valid Track ID; "
                    "receipt is uncertain.",
                    outcome_unknown=True,
                )
            track_id = track_ids[0].strip()
            if not _valid_invoice_track_id(track_id):
                raise SiiTransportError(
                    "The SII returned STATUS=0 with an invalid 10-position "
                    "invoice Track ID; receipt is uncertain.",
                    outcome_unknown=True,
                    track_id=track_id,
                )
            return {
                "track_id": track_id,
                "status": "ask_for_status",
                "diagnostic": _html_text(root, "GLOSA") or False,
            }
        raise SiiTransportError("The SII rejected the authentication token.")

    def _check_invoice_status(self, track_id, document_code):
        if not _valid_invoice_track_id(track_id):
            raise SiiTransportError(
                "The SII invoice Track ID must contain exactly 10 positions."
            )
        company_rut, company_dv = _rut_parts(self.company.vat)
        wsdl = SOAP_URLS[self.mode] + "QueryEstUp.jws?WSDL"
        root = None
        code = ""
        invalid_token_codes = {"5", "001", "002", "003"}
        token_code = ""
        for attempt in range(2):
            token = self._get_token(refresh=bool(attempt))
            result = self.soap_factory(wsdl).service.getEstUp(
                company_rut, company_dv, str(track_id), token
            )
            root = _parse_xml(result)
            code = _single_xml_value(root, "ESTADO")
            token_code = _single_xml_value(root, "TOKEN")
            if not code and token_code in invalid_token_codes:
                code = token_code
            if (
                code not in invalid_token_codes
                and token_code not in invalid_token_codes
            ):
                break
        if code in invalid_token_codes or token_code in invalid_token_codes:
            raise SiiTransportError(
                "SII authentication was rejected during status query "
                f"(TOKEN={token_code or code}).",
                response_metadata={
                    "sii_status_code": code or False,
                    "sii_token_code": token_code or code,
                },
            )
        if not code:
            raise SiiTransportError("The SII status response is incomplete.")
        response_track_id = _single_xml_value(root, "TRACKID")
        track_id_required_codes = {
            "SDK",
            "CRT",
            "PDR",
            "SOK",
            "RSC",
            "RFR",
            "RCT",
            "FOK",
            "EPR",
        }
        if not response_track_id and code in track_id_required_codes:
            raise SiiTransportError(
                "The SII status response has no Track ID for the queried upload.",
                response_metadata={"sii_status_code": code},
            )
        if response_track_id and response_track_id != str(track_id):
            raise SiiTransportError(
                "The SII status response belongs to a different Track ID.",
                response_metadata={"sii_status_code": code},
            )
        attention_number = _single_xml_value(root, "NUM_ATENCION")
        diagnostic = _single_xml_value(root, "GLOSA")
        result = {
            "status": _invoice_response_status(root, code),
            "diagnostic": diagnostic,
            "sii_status_code": code,
            "sii_server_code": _single_xml_value(root, "SRV_CODE") or False,
            "sii_sql_code": _single_xml_value(root, "SQL_CODE") or False,
            "sii_error_code": _single_xml_value(root, "ERR_CODE") or False,
            "sii_token_code": token_code or False,
            "sii_attention_number": attention_number or False,
            "sii_document_type": False,
            "sii_informed_count": False,
            "sii_accepted_count": False,
            "sii_rejected_count": False,
            "sii_objection_count": False,
        }
        if code == "EPR":
            result.update(_invoice_response_counters(root))
            if result["sii_document_type"] != str(document_code):
                result["status"] = "ask_for_status"
                result["diagnostic"] = (
                    "The SII response document type does not match the invoice."
                )
        return result

    def _send_boleta(self, xml_payload, filename):
        data = self._multipart_fields(xml_payload, filename)
        for attempt in range(2):
            token = self._get_token(rest=True, refresh=bool(attempt))
            headers = {
                "Accept": "application/json",
                "Cookie": f"TOKEN={token}",
                "User-Agent": "Mozilla/4.0 ( compatible; PROG 1.0; Windows NT)",
            }
            try:
                response = self.session.post(
                    BOLETA_URLS[self.mode]["send"],
                    data=data[0],
                    files={"archivo": data[1]},
                    headers=headers,
                    timeout=REST_TIMEOUT,
                )
            except requests.RequestException as error:
                raise SiiTransportError(
                    "The SII connection ended without confirming receipt.",
                    outcome_unknown=True,
                ) from error
            if response.status_code == 401 and attempt == 0:
                continue
            try:
                response.raise_for_status()
            except requests.HTTPError as error:
                raise SiiTransportError(
                    "The SII boleta endpoint returned an invalid response.",
                    outcome_unknown=response.status_code >= 500,
                ) from error
            try:
                payload = response.json()
            except ValueError as error:
                raise SiiTransportError(
                    "The SII boleta endpoint returned invalid JSON.",
                    outcome_unknown=True,
                ) from error
            if not isinstance(payload, dict):
                raise SiiTransportError(
                    "The SII boleta response is not a JSON object.",
                    outcome_unknown=True,
                )
            raw_track_id = payload.get("trackid") or payload.get("trackId")
            if isinstance(raw_track_id, bool):
                track_id = None
            elif isinstance(raw_track_id, (str, int)) and raw_track_id:
                track_id = str(raw_track_id).strip()
            else:
                track_id = None
            state = payload.get("estado")
            if state != "REC" or not _valid_boleta_track_id(track_id):
                diagnostic = payload.get("glosa") or (
                    "The SII boleta response did not confirm receipt with "
                    "estado=REC and a valid 15-digit Track ID."
                )
                raise SiiTransportError(
                    diagnostic,
                    outcome_unknown=True,
                    track_id=track_id,
                )
            return {
                "track_id": track_id,
                "status": "ask_for_status",
                "diagnostic": payload.get("glosa", ""),
            }
        raise SiiTransportError("The SII rejected the authentication token.")

    def _multipart_fields(self, xml_payload, filename):
        sender_rut, sender_dv, company_rut, company_dv = self._sender_data()
        return (
            {
                "rutSender": sender_rut,
                "dvSender": sender_dv,
                "rutCompany": company_rut,
                "dvCompany": company_dv,
            },
            (filename, xml_payload, "text/xml"),
        )

    def _check_boleta_status(self, track_id, document_code, folio):
        if not _valid_boleta_track_id(track_id):
            raise SiiTransportError(
                "The SII boleta Track ID must contain exactly 15 digits."
            )
        company_rut, company_dv = _rut_parts(self.company.vat)
        url = urljoin(
            BOLETA_URLS[self.mode]["auth"],
            f"boleta.electronica.envio/{company_rut}-{company_dv}-{track_id}",
        )
        for attempt in range(2):
            token = self._get_token(rest=True, refresh=bool(attempt))
            try:
                response = self.session.get(
                    url, headers={"Cookie": f"TOKEN={token}"}, timeout=REST_TIMEOUT
                )
                if response.status_code == 401 and attempt == 0:
                    continue
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError) as error:
                raise SiiTransportError(
                    "Could not query the SII boleta status."
                ) from error
            return _boleta_response_status(payload, document_code, folio)
        raise SiiTransportError("The SII rejected the authentication token.")


def _boleta_response_status(payload, document_code, folio):
    if not isinstance(payload, dict):
        raise SiiTransportError("The SII boleta status is not a JSON object.")
    if payload.get("estado") not in {"REC", "SOK", "EPR"}:
        raise SiiTransportError(
            "The SII boleta status has a missing or unknown estado."
        )
    details = payload.get("detalle_rep_rech") or []
    statistics = payload.get("estadistica") or []
    if not isinstance(details, list) or not isinstance(statistics, list):
        raise SiiTransportError("The SII boleta status lists are malformed.")
    for detail in details:
        if not isinstance(detail, dict):
            raise SiiTransportError("The SII boleta detail is malformed.")
        status = REST_STATUSES.get(detail.get("estado"))
        if detail.get("folio") is None and status == "rejected":
            return {"status": "rejected", "diagnostic": detail.get("glosa", "")}
        matches_code = str(detail.get("tipo")) == str(document_code)
        try:
            matches_folio = int(detail.get("folio")) == int(folio)
        except (TypeError, ValueError):
            matches_folio = False
        if status and matches_code and matches_folio:
            return {"status": status, "diagnostic": detail.get("glosa", "")}
    for summary in statistics:
        if not isinstance(summary, dict):
            raise SiiTransportError("The SII boleta statistics are malformed.")
        if str(summary.get("tipo")) != str(document_code):
            continue
        try:
            reported = int(summary.get("informados") or 0)
            accepted = int(summary.get("aceptados") or 0)
            objections = int(summary.get("reparos") or 0)
        except (TypeError, ValueError) as error:
            raise SiiTransportError(
                "The SII boleta statistics contain invalid counts."
            ) from error
        if reported and accepted == reported:
            return {"status": "accepted", "diagnostic": ""}
        if objections > 0:
            return {"status": "objected", "diagnostic": ""}
    return {"status": "ask_for_status", "diagnostic": ""}


def _valid_boleta_track_id(track_id):
    return isinstance(track_id, str) and re.fullmatch(r"\d{15}", track_id) is not None


def _valid_invoice_track_id(track_id):
    return isinstance(track_id, str) and len(track_id) == 10


def _parse_xml(value):
    if isinstance(value, str):
        value = value.encode("utf-8")
    if not value:
        raise SiiTransportError("The SII returned an empty response.")
    try:
        return etree.fromstring(value)
    except etree.XMLSyntaxError as error:
        raise SiiTransportError("The SII returned invalid XML.") from error


def _c14n(node):
    return etree.tostring(node, method="c14n", with_comments=False)


def _ascii(value):
    if isinstance(value, bytes):
        value = value.decode("ascii")
    return re.sub(r"\s+", "", value)


def _rut_parts(value):
    rut = normalize_rut(value).removeprefix("CL")
    if not re.fullmatch(r"\d+-[0-9K]", rut):
        raise SiiTransportError("The sender or company RUT is invalid.")
    number, verifier = rut.rsplit("-", 1)
    return number.lstrip("0") or "0", verifier


def _invoice_response_status(root, code):
    if code == "EPR":
        counters = _invoice_response_counters(root)
        informed = counters["sii_informed_count"]
        accepted = counters["sii_accepted_count"]
        rejected = counters["sii_rejected_count"]
        objections = counters["sii_objection_count"]
        if informed != 1 or accepted + rejected + objections != informed:
            return "ask_for_status"
        if accepted == informed:
            return "accepted"
        if rejected == informed:
            return "rejected"
        if objections == informed:
            return "objected"
        return "ask_for_status"
    if code in {"SDK", "CRT", "PDR", "-11", "SOK"}:
        return "ask_for_status"
    if code in {
        "-3",
        "PRD",
        "RCH",
        "RFR",
        "RSC",
        "RCT",
        "2",
        "106",
        "DNK",
        "RLV",
        "05",
    }:
        return "rejected"
    return "ask_for_status"


def _single_xml_value(root, local_name, required=False):
    values = [
        value.strip()
        for value in root.xpath(f"//*[local-name()='{local_name}']/text()")
    ]
    values = [value for value in values if value]
    if len(values) > 1 or (required and len(values) != 1):
        raise SiiTransportError(f"The SII status response has no unique {local_name}.")
    return values[0] if values else ""


def _invoice_response_counters(root):
    document_type = _single_xml_value(root, "TIPO_DOCTO", required=True)
    result = {"sii_document_type": document_type}
    for xml_name, result_name in (
        ("INFORMADOS", "sii_informed_count"),
        ("ACEPTADOS", "sii_accepted_count"),
        ("RECHAZADOS", "sii_rejected_count"),
        ("REPAROS", "sii_objection_count"),
    ):
        raw_value = _single_xml_value(root, xml_name, required=True)
        if not re.fullmatch(r"\d+", raw_value):
            raise SiiTransportError(
                f"The SII status response has an invalid {xml_name} count."
            )
        result[result_name] = int(raw_value)
    return result


def _html_text(root, local_name):
    values = _html_values(root, local_name)
    return " ".join(value.strip() for value in values if value.strip()) or ""


def _html_values(root, local_name):
    if root is None:
        return []
    values = root.xpath(
        "//*[translate(local-name(), 'abcdefghijklmnopqrstuvwxyz', "
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZ')=$name]/text()",
        name=local_name.upper(),
    )
    return values
