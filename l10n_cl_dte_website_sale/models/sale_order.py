# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from stdnum.cl import rut as cl_rut

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

# Taxable and exempt SII document codes for each checkout choice.
DOCUMENT_CODES = {"boleta": ("39", "41"), "factura": ("33", "34")}
GENERIC_RUT = "666666666"
# SII field sizes for RznSocRecep and GiroRecep.
RECEIVER_NAME_SIZE = 100
RECEIVER_ACTIVITY_SIZE = 40
# Receiver fields completed on an existing partner only when they are empty.
COMPLETABLE_FIELDS = (
    "l10n_latam_identification_type_id",
    "l10n_cl_sii_taxpayer_type",
    "l10n_cl_activity_description",
    "street",
    "street2",
    "city",
    "state_id",
    "zip",
    "country_id",
)


def compact_rut(value):
    """Return the RUT digits and check digit without separators."""
    return cl_rut.compact(value or "") if value else ""


def normalize_rut(value):
    """Return the RUT as ``12345678-5``, or an empty string when invalid."""
    text = (value or "").strip()
    if not text or not cl_rut.is_valid(text):
        return ""
    return cl_rut.format(text).replace(".", "").upper()


def clean_text(value):
    """Collapse whitespace of a submitted value."""
    return " ".join(str(value or "").split())


class SaleOrder(models.Model):
    _inherit = "sale.order"

    l10n_cl_dte_document_kind = fields.Selection(
        [
            ("boleta", "Electronic receipt (boleta)"),
            ("factura", "Electronic invoice (factura)"),
        ],
        string="Chilean Tax Document",
        copy=False,
        help="Tax document requested by the eCommerce customer. Website orders "
        "of Chilean companies without a choice are invoiced as a boleta.",
    )
    l10n_cl_dte_receiver_vat = fields.Char(string="Factura RUT", copy=False)
    l10n_cl_dte_receiver_name = fields.Char(string="Factura Business Name", copy=False)
    l10n_cl_dte_receiver_activity = fields.Char(
        string="Factura Activity (Giro)", copy=False
    )

    # --- Checkout choice ---------------------------------------------------

    def _l10n_cl_dte_checkout_applies(self):
        """Whether this order follows the Chilean boleta/factura checkout."""
        self.ensure_one()
        return (
            bool(self.website_id)
            and self.website_id._l10n_cl_dte_checkout_enabled()
            and self.company_id.account_fiscal_country_id.code == "CL"
        )

    @api.model
    def _l10n_cl_dte_parse_checkout_form(self, form):
        """Keep only the checkout keys of a submitted form, as clean text."""
        return {
            "kind": clean_text(form.get("l10n_cl_dte_document_kind")),
            "vat": clean_text(form.get("l10n_cl_dte_receiver_vat")),
            "name": clean_text(form.get("l10n_cl_dte_receiver_name")),
            "activity": clean_text(form.get("l10n_cl_dte_receiver_activity")),
        }

    def _l10n_cl_dte_stored_values(self):
        self.ensure_one()
        return {
            "kind": self.l10n_cl_dte_document_kind or "",
            "vat": self.l10n_cl_dte_receiver_vat or "",
            "name": self.l10n_cl_dte_receiver_name or "",
            "activity": self.l10n_cl_dte_receiver_activity or "",
        }

    def _l10n_cl_dte_checkout_errors(self, values):
        """Return the checkout errors by field name; empty when valid."""
        self.ensure_one()
        kind = values.get("kind")
        if kind not in DOCUMENT_CODES:
            return {"kind": self.env._("Choose either a boleta or a factura.")}
        if kind == "boleta":
            return {}
        return self._l10n_cl_dte_factura_errors(values)

    def _l10n_cl_dte_factura_errors(self, values):
        self.ensure_one()
        errors = {}
        vat = normalize_rut(values.get("vat"))
        if not vat or compact_rut(vat) == GENERIC_RUT:
            errors["vat"] = self.env._("Enter a valid Chilean RUT for the factura.")
        name = values.get("name") or ""
        if not name:
            errors["name"] = self.env._("Enter the business name for the factura.")
        elif len(name) > RECEIVER_NAME_SIZE:
            errors["name"] = self.env._(
                "The business name cannot exceed %s characters.", RECEIVER_NAME_SIZE
            )
        activity = values.get("activity") or ""
        if not activity:
            errors["activity"] = self.env._(
                "Enter the business activity (giro) for the factura."
            )
        elif len(activity) > RECEIVER_ACTIVITY_SIZE:
            errors["activity"] = self.env._(
                "The business activity cannot exceed %s characters.",
                RECEIVER_ACTIVITY_SIZE,
            )
        billing = self.partner_invoice_id
        if (
            billing.country_id.code != "CL"
            or not (billing.street or "").strip()
            or not (billing.city or "").strip()
        ):
            errors["address"] = self.env._(
                "A factura needs a billing address in Chile with street and commune."
            )
        return errors

    def _l10n_cl_dte_set_checkout_choice(self, values):
        """Store a validated choice; boletas drop any factura data."""
        self.ensure_one()
        is_factura = values["kind"] == "factura"
        self.write(
            {
                "l10n_cl_dte_document_kind": values["kind"],
                "l10n_cl_dte_receiver_vat": (
                    normalize_rut(values["vat"]) if is_factura else False
                ),
                "l10n_cl_dte_receiver_name": values["name"] if is_factura else False,
                "l10n_cl_dte_receiver_activity": (
                    values["activity"] if is_factura else False
                ),
            }
        )

    def _l10n_cl_dte_checkout_defaults(self):
        """Prefill the form from the order, else from the billing partner."""
        self.ensure_one()
        if self.l10n_cl_dte_document_kind:
            return self._l10n_cl_dte_stored_values()
        commercial = self.partner_invoice_id.commercial_partner_id
        if not self._l10n_cl_dte_has_own_rut(commercial):
            return {"kind": "boleta", "vat": "", "name": "", "activity": ""}
        return {
            "kind": (
                "factura" if commercial.l10n_cl_sii_taxpayer_type == "1" else "boleta"
            ),
            "vat": normalize_rut(commercial.vat),
            "name": commercial.name if commercial.is_company else "",
            "activity": commercial.l10n_cl_activity_description or "",
        }

    @api.model
    def _l10n_cl_dte_has_own_rut(self, partner):
        vat = normalize_rut(partner.vat)
        return (
            partner.country_id.code == "CL"
            and bool(vat)
            and compact_rut(vat) != GENERIC_RUT
        )

    def _check_cart_is_ready_to_be_paid(self):
        """Refuse payment while a requested factura lacks valid data."""
        result = super()._check_cart_is_ready_to_be_paid()
        for order in self:
            if (
                order._l10n_cl_dte_checkout_applies()
                and order.l10n_cl_dte_document_kind == "factura"
            ):
                errors = order._l10n_cl_dte_checkout_errors(
                    order._l10n_cl_dte_stored_values()
                )
                if errors:
                    raise ValidationError("\n".join(errors.values()))
        return result

    # --- Invoicing -----------------------------------------------------------

    def _prepare_invoice(self):
        """Carry the checkout choice to the invoice receiver and DTE type."""
        values = super()._prepare_invoice()
        if not self._l10n_cl_dte_checkout_applies():
            return values
        kind = self.l10n_cl_dte_document_kind or "boleta"
        values = dict(values, partner_id=self._l10n_cl_dte_invoice_receiver(kind).id)
        document_type = self._l10n_cl_dte_invoice_document_type(
            kind, values.get("journal_id")
        )
        if document_type:
            values["l10n_latam_document_type_id"] = document_type.id
        return values

    def _create_invoices(self, grouped=False, final=False, date=None):
        """Let customers follow invoices issued to another fiscal receiver."""
        moves = super()._create_invoices(grouped=grouped, final=final, date=date)
        for move in moves:
            receiver = move.partner_id.commercial_partner_id
            orders = move.line_ids.sale_line_ids.order_id.filtered("website_id")
            customers = orders.partner_id.filtered(
                lambda partner, receiver=receiver: partner.commercial_partner_id
                != receiver
            )
            if customers:
                move.sudo().message_subscribe(partner_ids=customers.ids)
        return moves

    def _l10n_cl_dte_invoice_receiver(self, kind):
        self.ensure_one()
        billing = self.partner_invoice_id
        if kind == "boleta":
            if self._l10n_cl_dte_has_own_rut(billing.commercial_partner_id):
                return billing
            return self.env.ref("l10n_cl.par_cfa")
        errors = self._l10n_cl_dte_checkout_errors(self._l10n_cl_dte_stored_values())
        if errors:
            raise UserError(
                self.env._(
                    "Order %(order)s cannot be invoiced as a factura:\n%(errors)s"
                )
                % {"order": self.name, "errors": "\n".join(errors.values())}
            )
        return self._l10n_cl_dte_factura_receiver()

    def _l10n_cl_dte_factura_receiver(self):
        """Return the partner that receives the factura.

        The customer's own billing partner is kept when it holds the RUT;
        otherwise a partner with that RUT is reused or created. Existing
        partners only get their empty fiscal fields completed, so checkout
        input never overwrites master data.
        """
        self.ensure_one()
        billing = self.partner_invoice_id
        receiver_values = self._l10n_cl_dte_receiver_values()
        vat = self.l10n_cl_dte_receiver_vat
        if compact_rut(billing.commercial_partner_id.vat) == compact_rut(vat):
            self._l10n_cl_dte_complete_partner(
                billing.commercial_partner_id.sudo(), receiver_values
            )
            return billing
        partners = self.env["res.partner"].sudo()
        receiver = partners.search(
            self._l10n_cl_dte_receiver_domain(vat), order="is_company desc, id", limit=1
        )
        if receiver:
            self._l10n_cl_dte_complete_partner(receiver, receiver_values)
        else:
            receiver = partners.create(receiver_values)
        return receiver.with_env(self.env)

    def _l10n_cl_dte_receiver_domain(self, vat):
        compact = compact_rut(vat)
        variants = {
            vat,
            compact,
            normalize_rut(vat).lower(),
            cl_rut.format(compact),
            cl_rut.format(compact).lower(),
        }
        return [
            ("vat", "in", list(variants)),
            ("parent_id", "=", False),
            ("company_id", "in", [False, self.company_id.id]),
        ]

    def _l10n_cl_dte_receiver_values(self):
        billing = self.partner_invoice_id
        return {
            "name": self.l10n_cl_dte_receiver_name,
            "vat": self.l10n_cl_dte_receiver_vat,
            "is_company": True,
            "company_id": self.company_id.id,
            "l10n_latam_identification_type_id": self.env.ref("l10n_cl.it_RUT").id,
            "l10n_cl_sii_taxpayer_type": "1",
            "l10n_cl_activity_description": self.l10n_cl_dte_receiver_activity,
            "street": billing.street,
            "street2": billing.street2,
            "city": billing.city,
            "state_id": billing.state_id.id,
            "zip": billing.zip,
            "country_id": billing.country_id.id,
            "email": billing.email,
            "phone": billing.phone,
        }

    @api.model
    def _l10n_cl_dte_complete_partner(self, partner, values):
        missing = {
            name: values[name]
            for name in COMPLETABLE_FIELDS
            if values.get(name) and not partner[name]
        }
        if missing:
            partner.write(missing)

    def _l10n_cl_dte_invoice_document_type(self, kind, journal_id):
        self.ensure_one()
        journals = self.env["account.journal"]
        journal = (
            journals.browse(journal_id)
            if journal_id
            else journals.search(
                [
                    *journals._check_company_domain(self.company_id),
                    ("type", "=", "sale"),
                ],
                limit=1,
            )
        )
        if not journal.l10n_latam_use_documents:
            return self.env["l10n_latam.document.type"]
        taxable_code, exempt_code = DOCUMENT_CODES[kind]
        code = taxable_code if self._l10n_cl_dte_has_vat_lines() else exempt_code
        document_type = self.env["l10n_latam.document.type"].search(
            [("country_id.code", "=", "CL"), ("code", "=", code)], limit=1
        )
        if not document_type:
            raise UserError(
                self.env._("Activate the Chilean document type %s first.", code)
            )
        return document_type

    def _l10n_cl_dte_has_vat_lines(self):
        """Whether any product line bears VAT; otherwise the sale is exempt."""
        self.ensure_one()
        lines = self.order_line.filtered(
            lambda line: not line.display_type and line.product_id
        )
        return any(
            tax.amount_type == "percent" and tax.amount > 0 for tax in lines.tax_id
        )
