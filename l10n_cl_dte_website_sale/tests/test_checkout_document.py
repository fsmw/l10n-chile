# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import Command, http
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.website.tools import MockRequest

from ..controllers.main import WebsiteSaleDteDocument

# Synthetic RUTs with valid check digits; they do not identify anyone.
CUSTOMER_RUT = "12345678-5"
BUSINESS_RUT = "76222222-1"
INVALID_RUT = "12345678-9"


def address_values(env, country):
    """Return a complete synthetic checkout address in ``country``."""
    return {
        "name": "Cliente Sintetico",
        "street": "Calle Prueba 123",
        "city": "Providencia",
        "state_id": env["res.country.state"]
        .search([("country_id", "=", country.id)], limit=1)
        .id,
        "country_id": country.id,
        "zip": "7500000",
        "phone": "+56 2 2000 0000",
        "email": "cliente.sintetico@example.com",
    }


@tagged("post_install", "-at_install")
class TestCheckoutDocument(AccountTestInvoicingCommon):
    @classmethod
    @AccountTestInvoicingCommon.setup_country("cl")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.chile = cls.env.ref("base.cl")
        cls.website = cls.env["website"].create(
            {"name": "Synthetic Chilean Shop", "company_id": cls.company.id}
        )
        cls.portal_user = new_test_user(
            cls.env,
            login="cl_dte_shop_customer",
            groups="base.group_portal",
            company_id=cls.company.id,
        )
        cls.customer = cls.portal_user.partner_id
        cls.customer.write(cls._address_values(cls.chile))
        cls.taxable_product = cls._create_product(
            name="Synthetic taxable service", type="service", lst_price=1000.0
        )
        cls.exempt_product = cls._create_product(
            name="Synthetic exempt service",
            type="service",
            lst_price=1000.0,
            taxes_id=[Command.clear()],
        )
        cls.controller = WebsiteSaleDteDocument()
        cls.portal_env = cls.env(user=cls.portal_user)

    @classmethod
    def _address_values(cls, country):
        return address_values(cls.env, country)

    def _cart(self, product=None, website=None, partner=None):
        website = website or self.website
        return (
            self.env["sale.order"]
            .with_company(website.company_id)
            .create(
                {
                    "partner_id": (partner or self.customer).id,
                    "website_id": website.id,
                    "company_id": website.company_id.id,
                    "order_line": [
                        Command.create(
                            {"product_id": (product or self.taxable_product).id}
                        )
                    ],
                }
            )
        )

    def _request(self, order, method="POST", website=None, **form):
        website = (website or self.website).with_env(self.portal_env)
        rendered = {}

        def render(template, values=None, **kwargs):
            rendered.update(values or {}, template=template)
            return "<rendered>"

        with MockRequest(
            self.portal_env, website=website, sale_order_id=order.id
        ) as req:
            req.httprequest.method = method
            req.render = render
            response = self.controller.shop_dte_document(**form)
        return response, rendered

    def _factura_form(self, **overrides):
        return {
            "l10n_cl_dte_document_kind": "factura",
            "l10n_cl_dte_receiver_vat": "76.222.222-1",
            "l10n_cl_dte_receiver_name": "Empresa Sintetica SpA",
            "l10n_cl_dte_receiver_activity": "Servicios de prueba",
            **overrides,
        }

    def _invoice(self, order):
        order.action_confirm()
        return order._create_invoices()

    # --- Checkout step -------------------------------------------------------

    def test_step_follows_delivery_on_chilean_website(self):
        steps = self.website._get_checkout_steps()
        xmlids = [step[0] for step in steps]
        delivery = xmlids.index(["website_sale.checkout", "website_sale.address"])
        self.assertEqual(
            xmlids[delivery + 1], ["l10n_cl_dte_website_sale.dte_document"]
        )
        self.assertEqual(steps[delivery][1]["main_button_href"], "/shop/dte_document")
        self.assertEqual(
            steps[delivery + 2][1]["back_button_href"], "/shop/dte_document"
        )

    def test_get_prefills_from_billing_partner(self):
        self.customer.write({"vat": CUSTOMER_RUT, "l10n_cl_sii_taxpayer_type": "1"})
        order = self._cart()
        _response, rendered = self._request(order, method="GET")
        self.assertEqual(rendered["template"], "l10n_cl_dte_website_sale.dte_document")
        self.assertEqual(rendered["dte_values"]["kind"], "factura")
        self.assertEqual(rendered["dte_values"]["vat"], CUSTOMER_RUT)
        self.assertFalse(order.l10n_cl_dte_document_kind)

    def test_boleta_without_rut_is_accepted(self):
        order = self._cart()
        response, _rendered = self._request(order, l10n_cl_dte_document_kind="boleta")
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.location.endswith("/shop/confirm_order"))
        self.assertEqual(order.l10n_cl_dte_document_kind, "boleta")
        self.assertFalse(order.l10n_cl_dte_receiver_vat)

    def test_factura_with_invalid_rut_is_refused(self):
        order = self._cart()
        _response, rendered = self._request(
            order, **self._factura_form(l10n_cl_dte_receiver_vat=INVALID_RUT)
        )
        self.assertEqual(rendered["template"], "l10n_cl_dte_website_sale.dte_document")
        self.assertIn("vat", rendered["dte_errors"])
        self.assertFalse(order.l10n_cl_dte_document_kind)

    def test_factura_with_generic_rut_or_missing_data_is_refused(self):
        order = self._cart()
        _response, rendered = self._request(
            order,
            **self._factura_form(
                l10n_cl_dte_receiver_vat="66666666-6",
                l10n_cl_dte_receiver_name="  ",
                l10n_cl_dte_receiver_activity="x" * 41,
            ),
        )
        self.assertEqual(set(rendered["dte_errors"]), {"vat", "name", "activity"})
        self.assertFalse(order.l10n_cl_dte_document_kind)

    def test_factura_needs_chilean_billing_address(self):
        self.customer.write(self._address_values(self.env.ref("base.us")))
        order = self._cart()
        _response, rendered = self._request(order, **self._factura_form())
        self.assertEqual(set(rendered["dte_errors"]), {"address"})

    def test_unknown_document_kind_is_refused(self):
        order = self._cart()
        _response, rendered = self._request(order, l10n_cl_dte_document_kind="ticket")
        self.assertIn("kind", rendered["dte_errors"])
        self.assertFalse(order.l10n_cl_dte_document_kind)

    def test_valid_factura_is_stored_normalized(self):
        order = self._cart()
        response, _rendered = self._request(order, **self._factura_form())
        self.assertEqual(response.status_code, 303)
        self.assertEqual(order.l10n_cl_dte_document_kind, "factura")
        self.assertEqual(order.l10n_cl_dte_receiver_vat, BUSINESS_RUT)
        self.assertEqual(order.l10n_cl_dte_receiver_name, "Empresa Sintetica SpA")

    def test_confirm_order_asks_for_the_document_first(self):
        order = self._cart()
        with MockRequest(
            self.portal_env,
            website=self.website.with_env(self.portal_env),
            sale_order_id=order.id,
        ):
            # Call the endpoint body: the test bypasses the HTTP dispatcher.
            endpoint = WebsiteSaleDteDocument.shop_confirm_order.original_endpoint
            response = endpoint(self.controller)
        self.assertTrue(response.location.endswith("/shop/dte_document"))

    def test_payment_refuses_factura_that_became_invalid(self):
        order = self._cart()
        self._request(order, **self._factura_form())
        self.customer.write({"country_id": self.env.ref("base.ar").id})
        with self.assertRaises(ValidationError):
            order._check_cart_is_ready_to_be_paid()

    def test_portal_customer_cannot_write_the_choice_directly(self):
        order = self._cart()
        with self.assertRaises(AccessError):
            order.with_user(self.portal_user).write(
                {"l10n_cl_dte_document_kind": "factura"}
            )

    # --- Invoice propagation -------------------------------------------------

    def test_boleta_invoice_goes_to_final_consumer_as_39(self):
        order = self._cart()
        self._request(order, l10n_cl_dte_document_kind="boleta")
        invoice = self._invoice(order)
        self.assertEqual(invoice.l10n_latam_document_type_id.code, "39")
        self.assertEqual(invoice.partner_id, self.env.ref("l10n_cl.par_cfa"))
        self.assertIn(self.customer, invoice.message_partner_ids)
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    def test_exempt_boleta_invoice_is_41_for_identified_customer(self):
        self.customer.write({"vat": CUSTOMER_RUT})
        order = self._cart(product=self.exempt_product)
        invoice = self._invoice(order)
        self.assertEqual(invoice.l10n_latam_document_type_id.code, "41")
        self.assertEqual(invoice.partner_id, self.customer)

    def test_factura_invoice_creates_receiver_as_33(self):
        order = self._cart()
        self._request(order, **self._factura_form())
        invoice = self._invoice(order)
        receiver = invoice.partner_id
        self.assertEqual(invoice.l10n_latam_document_type_id.code, "33")
        self.assertNotEqual(receiver, self.customer)
        self.assertEqual(receiver.vat, BUSINESS_RUT)
        self.assertEqual(receiver.name, "Empresa Sintetica SpA")
        self.assertEqual(receiver.l10n_cl_activity_description, "Servicios de prueba")
        self.assertEqual(receiver.l10n_cl_sii_taxpayer_type, "1")
        self.assertEqual(
            (receiver.street, receiver.city), ("Calle Prueba 123", "Providencia")
        )
        self.assertEqual(receiver.company_id, self.company)
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    def test_exempt_factura_reuses_partner_without_overwriting_it(self):
        existing = self.env["res.partner"].create(
            {
                "name": "Razon Social Registrada",
                "is_company": True,
                "vat": BUSINESS_RUT,
                "country_id": self.chile.id,
            }
        )
        order = self._cart(product=self.exempt_product)
        self._request(order, **self._factura_form())
        invoice = self._invoice(order)
        self.assertEqual(invoice.l10n_latam_document_type_id.code, "34")
        self.assertEqual(invoice.partner_id, existing)
        self.assertEqual(existing.name, "Razon Social Registrada")
        self.assertEqual(existing.l10n_cl_activity_description, "Servicios de prueba")

    def test_factura_for_own_rut_keeps_billing_partner(self):
        self.customer.write({"vat": BUSINESS_RUT})
        order = self._cart()
        self._request(order, **self._factura_form())
        invoice = self._invoice(order)
        self.assertEqual(invoice.partner_id, self.customer)
        self.assertEqual(self.customer.l10n_cl_sii_taxpayer_type, "1")

    # --- Other companies -----------------------------------------------------

    def test_non_chilean_website_is_unaffected(self):
        other = self.setup_other_company(
            name="Synthetic US Company",
            country_id=self.env.ref("base.us").id,
            currency_id=self.env.ref("base.USD").id,
        )["company"]
        website = self.env["website"].create(
            {"name": "Synthetic US Shop", "company_id": other.id}
        )
        xmlids = [step[0] for step in website._get_checkout_steps()]
        self.assertNotIn(["l10n_cl_dte_website_sale.dte_document"], xmlids)
        partner = self.env["res.partner"].create(
            {
                **self._address_values(self.env.ref("base.us")),
                "name": "Synthetic US Customer",
            }
        )
        product = self._create_product(
            name="Synthetic US service", type="service", taxes_id=[Command.clear()]
        )
        order = self._cart(product=product, website=website, partner=partner)
        self.assertFalse(order._l10n_cl_dte_checkout_applies())
        values = order._prepare_invoice()
        self.assertNotIn("l10n_latam_document_type_id", values)
        self.assertEqual(values["partner_id"], partner.id)


@tagged("post_install", "-at_install")
class TestCheckoutDocumentPage(AccountTestInvoicingCommon, HttpCase):
    """Render and submit the step through the real HTTP stack."""

    @classmethod
    @AccountTestInvoicingCommon.setup_country("cl")
    def setUpClass(cls):
        super().setUpClass()
        cls.website = cls.env.ref("website.default_website")
        cls.website.company_id = cls.company_data["company"]
        user = new_test_user(
            cls.env,
            login="cl_dte_page_customer",
            groups="base.group_portal",
            company_id=cls.website.company_id.id,
        )
        user.partner_id.write(address_values(cls.env, cls.env.ref("base.cl")))
        product = cls._create_product(
            name="Synthetic page service", type="service", lst_price=1000.0
        )
        cls.order = cls.env["sale.order"].create(
            {
                "partner_id": user.partner_id.id,
                "website_id": cls.website.id,
                "order_line": [Command.create({"product_id": product.id})],
            }
        )

    def test_page_renders_and_accepts_a_boleta(self):
        session = self.authenticate("cl_dte_page_customer", "cl_dte_page_customer")
        session["sale_order_id"] = self.order.id
        http.root.session_store.save(session)
        page = self.url_open("/shop/dte_document")
        self.assertEqual(page.status_code, 200)
        self.assertIn('name="l10n_cl_dte_receiver_vat"', page.text)
        response = self.url_open(
            "/shop/dte_document",
            data={
                "l10n_cl_dte_document_kind": "boleta",
                "csrf_token": http.Request.csrf_token(self),
            },
            allow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.order.l10n_cl_dte_document_kind, "boleta")
