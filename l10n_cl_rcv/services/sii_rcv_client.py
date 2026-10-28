# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import base64
import email.utils
import os
import secrets
import tempfile
import time

import requests

from odoo import _, api, models
from odoo.exceptions import UserError

BASE = "https://www4.sii.cl"
FACADE = BASE + "/consdcvinternetui/services/data/facadeService"
NAMESPACE = "cl.sii.sdi.lob.diii.consdcv.data.api.interfaces.FacadeService"
AUTH = "https://herculesr.sii.cl/cgi_AUT2000/CAutInicio.cgi"
TOKEN_CHECK = "https://zeusr.sii.cl/cgi_AUT2000/AutTknData.cgi"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
MAX_RETRIES = 1
MAX_RETRY_AFTER = 30
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 30


class SiiRcvClient(models.AbstractModel):
    _name = "l10n.cl.rcv.sii.client"
    _description = "Read-only SII RCV transport"

    @api.model
    def _get_signature(self, company, user_id=False):
        """Return a valid certificate for the company.

        ``certificate.certificate`` is a Community module: it always provides
        ``company_id`` and ``is_valid``. The ``user_id`` owner field belongs to
        the DTE localization when it is installed, so it is used only when the
        field exists and falls back to the shared certificate otherwise.
        """
        Certificate = self.env["certificate.certificate"]
        domain = [("company_id", "=", company.id), ("is_valid", "=", True)]
        if "user_id" in Certificate._fields:
            signature = Certificate.search(
                domain + [("user_id", "=", user_id)], limit=1
            )
            if signature:
                return signature
            return Certificate.search(domain + [("user_id", "=", False)], limit=1)
        return Certificate.search(domain, limit=1)

    @api.model
    def _fetch(self, sync):
        if sync.operation == "boleta":
            raise UserError(
                _(
                    "La consulta directa de boletas aún no tiene un contrato SII "
                    "validado."
                )
            )
        company = sync.company_id
        signature = self._get_signature(company, sync.requested_by.id)
        if not signature or not signature.is_valid:
            raise UserError(
                _("No hay un certificado válido para la compañía seleccionada.")
            )
        if not company.vat:
            raise UserError(_("La compañía no tiene RUT configurado."))
        company_vat = self.env["l10n.cl.rcv.normalizer"]._vat(company.vat)
        if "-" not in company_vat:
            raise UserError(_("El RUT de la compañía no tiene formato válido."))
        rut, dv = company_vat.split("-", 1)
        cert_pem = base64.b64decode(
            signature.with_context(bin_size=False).pem_certificate
        )
        private_key = signature.private_key_id
        if not cert_pem or not private_key or not private_key.pem_key:
            raise UserError(
                _(
                    "El certificado de la compañía no contiene una llave privada "
                    "utilizable."
                )
            )
        key_pem = base64.b64decode(private_key.with_context(bin_size=False).pem_key)
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Referer": BASE + "/consdcvinternetui/",
        }
        with tempfile.TemporaryDirectory(prefix="odoo-rcv-") as temp_dir:
            cert_path, key_path = temp_dir + "/cert.pem", temp_dir + "/key.pem"
            for path, content in ((cert_path, cert_pem), (key_path, key_pem)):
                with open(path, "wb") as cert_file:
                    cert_file.write(content)
                os.chmod(path, 0o600)
            with requests.Session() as session:
                session.headers.update({"User-Agent": USER_AGENT})
                session.cert = (cert_path, key_path)
                session.verify = True
                self._check_bootstrap(
                    session.post(
                        AUTH,
                        data={"referencia": BASE + "/consdcvinternetui/"},
                        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                    ),
                    "inicio de sesión",
                )
                token = session.cookies.get("TOKEN")
                if not token:
                    raise UserError(_("El inicio de sesión SII no entregó un token."))
                self._check_bootstrap(
                    session.get(
                        BASE + "/consdcvinternetui/",
                        headers={"Accept": "text/html"},
                        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                    ),
                    "carga de RCV",
                )
                self._check_bootstrap(
                    session.post(
                        BASE + "/common-1.0/services/autConfDataService/obtieneConf",
                        json={},
                        headers=headers,
                        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                    ),
                    "configuración de sesión",
                )
                self._check_bootstrap(
                    session.get(
                        BASE + "/common-1.0/services/aaSessionService/load",
                        headers=headers,
                        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                    ),
                    "carga de sesión",
                )
                stamp = int(time.time() * 1000)
                self._check_bootstrap(
                    session.get(
                        TOKEN_CHECK,
                        params={
                            "rnd": secrets.randbelow(10**9) / 10**9,
                            "callback": f"jQuery_{stamp}",
                            "_": stamp + 1,
                        },
                        headers={
                            "Accept": "*/*",
                            "Referer": BASE + "/consdcvinternetui/",
                        },
                        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                    ),
                    "validación de sesión",
                )
                self._check_bootstrap(
                    session.post(
                        BASE + "/consdcvinternetui/services/data/settingsService/"
                        "consultarParametros",
                        json=self._wrap("consultarParametros", {}, token),
                        headers=headers,
                        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                    ),
                    "parámetros de sesión",
                    require_json=True,
                )
                self._call(session, "getDatosInicio", {}, token, headers)
                period = f"{sync.period_id.year:04d}{sync.period_id.month:02d}"
                operations = [
                    ("COMPRA", ["REGISTRO", "RECLAMADO", "PENDIENTE"])
                    if sync.operation == "purchase"
                    else ("VENTA", ["REGISTRO"])
                ]
                result = []
                for operation, states in operations:
                    for state in states:
                        args = {
                            "rutEmisor": rut,
                            "dvEmisor": dv,
                            "ptributario": period,
                            "operacion": operation,
                            "estadoContab": state,
                        }
                        try:
                            summary = self._call(
                                session,
                                "getResumen",
                                {**args, "busquedaInicial": True},
                                token,
                                headers,
                                require_code=True,
                            )
                        except UserError:
                            result.append(
                                {
                                    "state": state,
                                    "fetch_error": "SiiSummaryError",
                                    "summary": {"data": []},
                                    "details": {},
                                    "unavailable_details": [],
                                }
                            )
                            continue
                        rows = summary.get("data")
                        if not isinstance(rows, list) or any(
                            not isinstance(row, dict) for row in rows
                        ):
                            result.append(
                                {
                                    "state": state,
                                    "fetch_error": "InvalidSummary",
                                    "summary": {"data": []},
                                    "details": {},
                                    "unavailable_details": [],
                                }
                            )
                            continue
                        types = sorted(
                            {
                                str(row.get("rsmnTipoDocInteger"))
                                for row in rows
                                if row.get("rsmnTipoDocInteger") is not None
                            }
                        )
                        details = {}
                        unavailable_details = []
                        method = (
                            "getDetalleCompra"
                            if operation == "COMPRA"
                            else "getDetalleVenta"
                        )
                        for type_code in types:
                            detail_args = {
                                **args,
                                "codTipoDoc": type_code,
                                "accionRecaptcha": "RCV_DETC"
                                if operation == "COMPRA"
                                else "RCV_DETV",
                            }
                            try:
                                detail = self._call(
                                    session,
                                    method,
                                    detail_args,
                                    token,
                                    headers,
                                    require_code=True,
                                    allowed_functional_codes=(99,)
                                    if operation == "COMPRA" and state == "PENDIENTE"
                                    else (),
                                )
                            except UserError:
                                details[type_code] = None
                                unavailable_details.append(type_code)
                                continue
                            detail_rows = detail.get("data")
                            if not isinstance(detail_rows, list) or any(
                                not isinstance(row, dict) for row in detail_rows
                            ):
                                details[type_code] = None
                                unavailable_details.append(type_code)
                                continue
                            if (
                                str(
                                    (detail.get("respEstado") or {}).get("codRespuesta")
                                )
                                == "99"
                            ):
                                details[type_code] = None
                                unavailable_details.append(type_code)
                            else:
                                details[type_code] = detail_rows
                        result.append(
                            {
                                "state": state,
                                "summary": summary,
                                "details": details,
                                "unavailable_details": unavailable_details,
                            }
                        )
                return result

    @api.model
    def _wrap(self, method, data, token):
        return {
            "metaData": {
                "namespace": f"{NAMESPACE}/{method}",
                "conversationId": token,
                "transactionId": secrets.token_hex(16),
            },
            "data": data,
        }

    @api.model
    def _check_bootstrap(self, response, step, require_json=False):
        if response.status_code < 200 or response.status_code >= 300:
            raise UserError(
                _(
                    "Falló la etapa de %(step)s del servicio SII (HTTP %(code)s).",
                    step=step,
                    code=response.status_code,
                )
            )
        if require_json:
            try:
                payload = response.json()
            except ValueError as exc:
                raise UserError(
                    _("El SII devolvió una respuesta no JSON en %s.") % step
                ) from exc
            if not isinstance(payload, dict):
                raise UserError(
                    _("El SII devolvió una respuesta inesperada en %s.") % step
                )
            response_state = payload.get("respEstado")
            if (
                isinstance(response_state, dict)
                and str(response_state.get("codRespuesta")) != "0"
            ):
                raise UserError(_("El SII rechazó la etapa de %s.") % step)

    @api.model
    def _call(
        self,
        session,
        method,
        data,
        token,
        headers,
        require_code=False,
        allowed_functional_codes=(),
    ):
        response = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = session.post(
                    FACADE + "/" + method,
                    json=self._wrap(method, data, token),
                    headers=headers,
                    timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                )
            except requests.exceptions.RequestException as exc:
                if attempt < MAX_RETRIES:
                    time.sleep(1)
                    continue
                raise UserError(
                    _("No fue posible conectar con el servicio RCV del SII.")
                ) from exc
            if response.status_code == 429 and attempt < MAX_RETRIES:
                time.sleep(self._retry_after(response.headers.get("Retry-After")))
                continue
            if response.status_code >= 500 and attempt < MAX_RETRIES:
                time.sleep(1)
                continue
            break
        if response.status_code == 429:
            raise UserError(
                _("El SII limitó temporalmente las consultas; reintente más tarde.")
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise UserError(
                _("El servicio RCV del SII respondió HTTP %s.") % response.status_code
            )
        try:
            result = response.json()
        except ValueError as exc:
            raise UserError(_("El SII devolvió una respuesta no JSON.")) from exc
        if not isinstance(result, dict):
            raise UserError(
                _("El SII devolvió una respuesta JSON con formato inesperado.")
            )
        code = (result.get("respEstado") or {}).get("codRespuesta")
        allowed_codes = {str(item) for item in allowed_functional_codes}
        if (code is not None and str(code) not in {"0", *allowed_codes}) or (
            require_code and code is None
        ):
            raise UserError(
                _("El SII rechazó la consulta (código funcional %s).") % code
            )
        if result.get("data") is None:
            result["data"] = []
        return result

    @api.model
    def _retry_after(self, value):
        if not value:
            return 1
        try:
            return min(max(int(value), 1), MAX_RETRY_AFTER)
        except (TypeError, ValueError):
            try:
                retry_at = email.utils.parsedate_to_datetime(value)
                return min(
                    max(int(retry_at.timestamp() - time.time()), 1), MAX_RETRY_AFTER
                )
            except (TypeError, ValueError, OverflowError):
                return 1
