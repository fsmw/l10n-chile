# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import email_normalize

from odoo.addons.l10n_cl_dte_inbox.models.incoming_import import ENVIRONMENTS
from odoo.addons.l10n_cl_dte_signing.services.signature_service import (
    SignatureService,
)

from ..services.exchange_composer import (
    RESULT_GLOSSES,
    chile_timestamp,
    compose_receipts,
    compose_reception,
    compose_result,
)
from ..services.exchange_signer import ExchangeSigner
from ..services.reception_status import envelope_status, reception_lines, reportable
from ..services.source_facts import format_rut, read_envelope, read_single_dte

EXCHANGE_KINDS = [
    ("reception", "Envelope Reception (RecepcionEnvio)"),
    ("result", "Commercial Result (ResultadoDTE)"),
    ("receipt", "Receipt of Goods or Services (EnvioRecibos)"),
]
SIGNING_ENVIRONMENTS = frozenset({"certification", "production"})
TEMPLATE = "l10n_cl_dte_receiver_responses.mail_template_exchange_response"
MAIL_STATES = {
    "outgoing": "queued",
    "sent": "sent",
    "received": "sent",
    "exception": "failed",
    "cancel": "failed",
}


class IncomingDteExchange(models.Model):
    _name = "l10n_cl.incoming.dte.exchange"
    _description = "Incoming DTE Exchange Response"
    _order = "id desc"
    _check_company_auto = True

    name = fields.Char(required=True, readonly=True)
    kind = fields.Selection(EXCHANGE_KINDS, required=True, readonly=True, index=True)
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, index=True, ondelete="restrict"
    )
    environment = fields.Selection(ENVIRONMENTS, required=True, readonly=True)
    import_id = fields.Many2one(
        "l10n_cl.incoming.dte.import",
        string="Received File",
        readonly=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    registry_response_id = fields.Many2one(
        "l10n_cl.incoming.dte.response",
        string="SII Registry Action",
        readonly=True,
        index=True,
        ondelete="restrict",
    )
    trigger = fields.Selection(
        [("manual", "Manual"), ("registry_auto", "Automatic After SII Registry")],
        required=True,
        default="manual",
        readonly=True,
    )
    line_ids = fields.One2many(
        "l10n_cl.incoming.dte.exchange.line", "exchange_id", readonly=True
    )
    responder_vat = fields.Char(string="Responder RUT", readonly=True)
    supplier_vat = fields.Char(string="Supplier RUT", required=True, readonly=True)
    signer_vat = fields.Char(string="Signer RUT", readonly=True)
    recipient_email = fields.Char(
        help="Supplier DTE exchange mailbox. It can be corrected before sending."
    )
    signed_xml = fields.Binary(string="Signed XML", attachment=True, readonly=True)
    filename = fields.Char(readonly=True)
    xml_sha256 = fields.Char(string="XML SHA-256", readonly=True, index=True)
    signed_at = fields.Datetime(readonly=True)
    mail_id = fields.Many2one(
        "mail.mail", string="Last Email", readonly=True, index=True, ondelete="set null"
    )
    send_count = fields.Integer(readonly=True)
    last_sent_at = fields.Datetime(readonly=True)
    state = fields.Selection(
        [
            ("signed", "Signed"),
            ("queued", "Queued"),
            ("sent", "Sent"),
            ("failed", "Failed"),
        ],
        compute="_compute_state",
        store=True,
        index=True,
    )
    send_error = fields.Text(related="mail_id.failure_reason")

    @api.depends("mail_id", "mail_id.state", "send_count")
    def _compute_state(self):
        for exchange in self:
            if exchange.mail_id:
                exchange.state = MAIL_STATES.get(exchange.mail_id.state, "queued")
            else:
                exchange.state = "sent" if exchange.send_count else "signed"

    def init(self):
        # One envelope reception per received file; results and receipts are
        # unique per DTE on the lines.
        self.env.cr.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "l10n_cl_incoming_dte_exchange_reception_uniq "
            "ON l10n_cl_incoming_dte_exchange (import_id) WHERE kind = 'reception'"
        )

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super  # noqa: ARG002
        raise UserError(_("Exchange responses are prepared from incoming DTEs."))

    def write(self, vals):
        if set(vals) - {"recipient_email"}:
            raise UserError(_("Signed exchange responses are immutable."))
        self._check_manager()
        if any(exchange.state not in {"signed", "failed"} for exchange in self):
            raise UserError(_("The recipient can only change before sending."))
        return super().write(vals)

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("Exchange response evidence must be retained."))

    def _write_internal(self, vals):
        return super().write(vals)

    @api.model
    def _check_manager(self):
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(_("Only a DTE manager can send exchange responses."))

    @api.model
    def _create_signed(self, values, line_values, compose):
        """Create, sign and store one response; ``compose(cover, signer)``."""
        self._check_manager()
        if values["environment"] not in SIGNING_ENVIRONMENTS:
            raise UserError(
                _(
                    "Exchange responses are signed only in the Certification or "
                    "Production SII environment."
                )
            )
        exchange = (
            super(IncomingDteExchange, self.sudo()).create([values]).with_env(self.env)
        )
        self.env["l10n_cl.incoming.dte.exchange.line"]._create_internal(
            [dict(line, exchange_id=exchange.id) for line in line_values]
        )
        exchange._sign(compose)
        return exchange

    def _sign(self, compose):
        self.ensure_one()
        kind = dict(self._fields["kind"].selection)[self.kind]
        profile, holder = SignatureService().signing_identity(
            self.company_id, self.environment, manager_only=True
        )
        cover = {
            "responder_vat": format_rut(self.company_id.partner_id.vat),
            "recipient_vat": self.supplier_vat,
            "response_id": self.id,
            "timestamp": chile_timestamp(),
        }
        if not cover["responder_vat"]:
            raise UserError(_("Set the company RUT before answering a supplier."))
        signer = format_rut(holder)
        signed = ExchangeSigner(profile.sudo().certificate_id).sign(
            compose(cover, signer)
        )
        root_name = "EnvioRecibos" if self.kind == "receipt" else "RespuestaDTE"
        self._write_internal(
            {
                "name": f"{kind} {self.id}",
                "signed_xml": base64.b64encode(signed),
                "filename": f"{root_name}_{self.supplier_vat}_{self.id}.xml",
                "xml_sha256": hashlib.sha256(signed).hexdigest(),
                "signed_at": fields.Datetime.now(),
                "responder_vat": cover["responder_vat"],
                "signer_vat": signer,
            }
        )
        for document in self.line_ids.incoming_document_id:
            document._create_event(
                f"exchange_{self.kind}_signed",
                document.state,
                document.state,
                self.xml_sha256,
            )

    def _signed_bytes(self):
        self.ensure_one()
        content = base64.b64decode(
            self.with_context(bin_size=False).signed_xml or b"", validate=True
        )
        if not content or hashlib.sha256(content).hexdigest() != self.xml_sha256:
            raise UserError(_("The stored exchange XML does not match its digest."))
        return content

    def action_send(self):
        """Email a signed response that was not emailed yet."""
        self._check_manager()
        for exchange in self:
            if exchange.state not in {"signed", "failed"}:
                raise UserError(
                    _("This response was already emailed. Use Resend to send it again.")
                )
            exchange._email()
        return True

    def action_resend(self):
        """Email the same signed XML again, on the user's explicit request."""
        self._check_manager()
        for exchange in self:
            if exchange.state == "signed":
                raise UserError(_("Send the response before resending it."))
            exchange._email()
        return True

    def _email_pending(self):
        """Email the signed responses that have a recipient; keep the others.

        A response without a supplier address stays signed so the user can set
        the address and send it instead of losing the signature.
        """
        ready = self.filtered(
            lambda item: item.state == "signed" and item.recipient_email
        )
        for exchange in ready:
            exchange._email()
        return ready

    def _email(self):
        self.ensure_one()
        recipient = email_normalize(self.recipient_email or "")
        if not recipient:
            raise UserError(
                _("Set the supplier's DTE exchange email before sending the response.")
            )
        attachment = (
            self.env["ir.attachment"]
            .sudo()
            .create(
                {
                    "name": self.filename,
                    "raw": self._signed_bytes(),
                    "mimetype": "application/xml",
                    "res_model": self._name,
                    "res_id": self.id,
                }
            )
        )
        mail_id = (
            self.env.ref(TEMPLATE)
            .sudo()
            .send_mail(
                self.id,
                email_values={
                    "email_to": recipient,
                    "recipient_ids": [],
                    "attachment_ids": attachment.ids,
                },
            )
        )
        self._write_internal(
            {
                "mail_id": mail_id,
                "send_count": self.send_count + 1,
                "last_sent_at": fields.Datetime.now(),
            }
        )
        for document in self.line_ids.incoming_document_id:
            document._create_event(
                f"exchange_{self.kind}_emailed",
                document.state,
                document.state,
                self.xml_sha256,
            )
        return mail_id

    def action_open(self):
        action = {
            "type": "ir.actions.act_window",
            "name": _("Exchange Responses"),
            "res_model": self._name,
            "domain": [("id", "in", self.ids)],
            "view_mode": "list,form",
        }
        if len(self) == 1:
            action.update({"res_id": self.id, "view_mode": "form"})
        return action

    # Preparation ------------------------------------------------------------

    @api.model
    def _prepare_reception(self, batch):
        """Return the signed RecepcionEnvio of one received file (once per file)."""
        self._check_manager()
        batch.ensure_one()
        existing = self.search(
            [("kind", "=", "reception"), ("import_id", "=", batch.id)], limit=1
        )
        if existing:
            return existing
        envelope = self._read_source(batch, read_envelope)
        if not envelope["issuer_vat"]:
            raise UserError(_("The received file does not identify its issuer."))
        company = batch.company_id
        company_vat = format_rut(company.partner_id.vat)
        lines = reception_lines(batch, envelope, company_vat)
        state, gloss = envelope_status(batch, envelope, company_vat)
        reception = {
            "name": batch.filename or batch.name,
            "received_at": chile_timestamp(batch.imported_at),
            "envelope_code": batch.id,
            "envelope_id": envelope["envelope_id"],
            "digest": envelope["digest"],
            "issuer_vat": envelope["issuer_vat"],
            "receiver_vat": envelope["receiver_vat"],
            "state": state,
            "gloss": gloss,
            "lines": lines,
        }
        values = self._exchange_values(
            "reception",
            company,
            batch.environment,
            envelope["issuer_vat"],
            envelope["documents"],
        )
        values["import_id"] = batch.id

        def compose(cover, _signer):
            return compose_reception(cover, reception)

        return self._create_signed(
            values, [self._line_values(line) for line in lines], compose
        )

    @api.model
    def _prepare_documents(
        self, kind, documents, state="0", gloss="", site="", **options
    ):
        """Return the result or receipt responses of DTEs from one supplier.

        DTEs already answered with this kind keep their response; only the
        others get a new signed response. ``options`` may carry
        ``registry_response`` and ``trigger``.
        """
        self._check_manager()
        if kind not in {"result", "receipt"} or state not in RESULT_GLOSSES:
            raise UserError(_("Choose a commercial result or a receipt of goods."))
        answered = self.env["l10n_cl.incoming.dte.exchange.line"].search(
            [("kind", "=", kind), ("incoming_document_id", "in", documents.ids)]
        )
        pending = documents - answered.incoming_document_id
        if not pending:
            return answered.exchange_id
        facts = self._document_facts(pending)
        suppliers = {item["issuer_vat"] for _document, item in facts}
        environments = set(pending.mapped("sii_environment"))
        if (
            len(suppliers) != 1
            or len(pending.company_id) != 1
            or len(environments) != 1
        ):
            raise UserError(
                _("Answer the DTEs of one supplier, company and environment at once.")
            )
        company = pending.company_id
        values = self._exchange_values(
            kind,
            company,
            environments.pop(),
            suppliers.pop(),
            [item for _document, item in facts],
        )
        registry = options.get("registry_response")
        values.update(
            registry_response_id=registry.id if registry else False,
            trigger=options.get("trigger", "manual"),
        )
        if kind == "result":
            lines, compose = self._result_lines(facts, state, gloss)
        else:
            lines, compose = self._receipt_lines(
                facts, site or self._default_site(company)
            )
        exchange = self._create_signed(
            values, [self._line_values(line) for line in lines], compose
        )
        return answered.exchange_id | exchange

    @api.model
    def _result_lines(self, facts, state, gloss):
        text = (gloss or "").strip() or RESULT_GLOSSES[state]
        lines = [
            dict(
                item,
                state=state,
                gloss=text[:256],
                envelope_code=self._envelope_code(document),
                document=document,
            )
            for document, item in facts
        ]

        def compose(cover, _signer):
            return compose_result(cover, lines)

        return lines, compose

    @api.model
    def _receipt_lines(self, facts, site):
        site = (site or "").strip()[:80]
        if not site:
            raise UserError(
                _("Set the place where the goods or services were received.")
            )
        lines = [
            dict(item, state="", gloss=site, document=document)
            for document, item in facts
        ]

        def compose(cover, signer):
            receipts = [
                dict(
                    line,
                    id=f"T{line['document_code']}F{line['folio']}",
                    site=site,
                    signer_vat=signer,
                )
                for line in lines
            ]
            return compose_receipts(cover, receipts)

        return lines, compose

    @api.model
    def _document_facts(self, documents):
        facts = []
        for document in documents:
            if document.state != "reviewed":
                raise UserError(
                    _(
                        "Review incoming DTE %(name)s before answering it.",
                        name=document.name,
                    )
                )
            item = self._read_source(document, read_single_dte)
            if not reportable(item):
                raise UserError(
                    _(
                        "DTE type %(code)s has no exchange response format.",
                        code=item["document_code"],
                    )
                )
            facts.append((document, item))
        return facts

    @api.model
    def _read_source(self, record, reader):
        """Read the stored received XML after checking its evidence digest."""
        stored = record.sudo().with_context(bin_size=False)
        content = base64.b64decode(stored.raw_xml or b"", validate=True)
        if not content or hashlib.sha256(content).hexdigest() != stored.xml_sha256:
            raise UserError(_("The stored received XML does not match its digest."))
        try:
            return reader(content, self.env._)
        except ValidationError as error:
            raise UserError(
                _("The received XML cannot be answered: %(error)s", error=error)
            ) from error

    @api.model
    def _envelope_code(self, document):
        line = document.sudo().import_line_ids.sorted("id")[:1]
        return line.import_id.id or document.id

    @api.model
    def _default_site(self, company):
        partner = company.partner_id
        address = ", ".join(part for part in (partner.street, partner.city) if part)
        return company.l10n_cl_dte_exchange_site or address or company.name

    @api.model
    def _line_values(self, line):
        document = line.get("document")
        return {
            "incoming_document_id": document.id if document else False,
            "document_code": line["document_code"],
            "folio": line["folio"],
            "issuer_vat": line["issuer_vat"],
            "issue_date": line["issue_date"],
            "total_amount": line["total"],
            "status_code": line["state"] or False,
            "gloss": line["gloss"],
        }

    @api.model
    def _exchange_values(self, kind, company, environment, supplier_vat, facts):
        return {
            "name": "/",
            "kind": kind,
            "company_id": company.id,
            "environment": environment,
            "supplier_vat": supplier_vat,
            "recipient_email": self._supplier_email(company, supplier_vat, facts),
        }

    @api.model
    def _supplier_email(self, company, supplier_vat, facts):
        """Prefer a partner's DTE exchange mailbox, then the DTE's issuer email."""
        partners = self._supplier_partners(company, supplier_vat)
        candidates = []
        if "l10n_cl_dte_email" in partners._fields:
            candidates += partners.mapped("l10n_cl_dte_email")
        candidates += [item["issuer_email"] for item in facts]
        candidates += partners.mapped("email")
        for candidate in candidates:
            normalized = email_normalize(candidate or "")
            if normalized:
                return normalized
        return False

    @api.model
    def _supplier_partners(self, company, supplier_vat):
        body, _separator, digit = supplier_vat.partition("-")
        compact = body + digit
        variants = [
            supplier_vat,
            compact,
            "CL" + compact,
            f"{int(body):,}".replace(",", ".") + "-" + digit,
        ]
        partners = (
            self.env["res.partner"]
            .sudo()
            .search(
                [("vat", "in", variants), ("company_id", "in", [False, company.id])],
                order="id",
            )
        )
        # Never answer to our own company contact.
        return partners.commercial_partner_id - company.partner_id
