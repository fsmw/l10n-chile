# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase


class ExportTestCase(DteInvoiceTestCase):
    """Synthetic export invoices with complete customs data."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.document_types["110"] = cls.env["l10n_latam.document.type"].search(
            [("country_id", "=", cls.chile.id), ("code", "=", "110")], limit=1
        )
        cls.usd = cls.env.ref("base.USD")
        cls.usd.sudo().active = True
        cls.usa = cls.env.ref("base.us")
        cls.buyer = cls.env["res.partner"].create(
            {
                "name": "Buyer Inc",
                "is_company": True,
                "street": "100 Main Street",
                "city": "Miami",
                "country_id": cls.usa.id,
            }
        )
        cls.catalog = {
            kind: cls._catalog(kind, code)
            for kind, code in (
                ("payment", "1"),
                ("sale_mode", "1"),
                ("sale_clause", "5"),
                ("transport", "1"),
                ("port", "906"),
                ("unit", "6"),
                ("package", "13"),
            )
        }

    @classmethod
    def _catalog(cls, kind, code):
        return (
            cls.env["l10n_cl.dte.export.catalog"]
            .sudo()
            .create(
                {
                    "kind": kind,
                    "code": code,
                    "name": f"{kind} {code}",
                    "source_url": "https://www.aduana.cl/",
                    "source_date": "2026-01-01",
                }
            )
        )

    def _export(self, currency=None, **values):
        invoice = self.init_invoice(
            "out_invoice",
            partner=self.buyer,
            invoice_date="2026-01-15",
            products=[self.product_a],
            taxes=[],
            currency=currency or self.usd,
        )
        invoice.invoice_line_ids.write(
            {"quantity": 2, "price_unit": 1234.5, "tax_ids": [(5, 0, 0)]}
        )
        catalog = self.catalog
        invoice.write(
            {
                "l10n_latam_document_type_id": self.document_types["110"].id,
                "dte_export_service": "goods",
                "dte_export_receiver_id": "EIN-12-3456789",
                "dte_export_nationality_id": self.usa.id,
                "dte_export_destination_id": self.usa.id,
                "dte_export_rate": 950.25,
                "dte_export_rate_date": "2026-01-15",
                "dte_export_rate_source": "BCCh observed dollar 2026-01-15",
                "dte_export_payment_id": catalog["payment"].id,
                "dte_export_sale_mode_id": catalog["sale_mode"].id,
                "dte_export_sale_clause_id": catalog["sale_clause"].id,
                "dte_export_clause_total": 2469,
                "dte_export_transport_id": catalog["transport"].id,
                "dte_export_loading_port_id": catalog["port"].id,
                "dte_export_gross_weight": 120.5,
                "dte_export_gross_unit_id": catalog["unit"].id,
                "dte_export_package_ids": [
                    (0, 0, {"type_id": catalog["package"].id, "quantity": 4})
                ],
                "dte_export_reference_ids": [
                    (
                        0,
                        0,
                        {
                            "document_type": "807",
                            "folio": "6001234567",
                            "date": "2026-01-14",
                        },
                    )
                ],
                **values,
            }
        )
        invoice.action_post()
        return invoice


# Lifecycle steps after signing, with the evidence each one records.
REMAINING_STEPS = (
    ("queued", "sii_request"),
    ("submitted", "sii_response"),
    ("accepted", "status_response"),
)


class ExportNoteCase(ExportTestCase):
    """An accepted export 110 with CAF for its notes 111 and 112."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("111", "112"):
            cls.document_types[code] = cls.env["l10n_latam.document.type"].search(
                [("country_id", "=", cls.chile.id), ("code", "=", code)], limit=1
            )

    def setUp(self):
        super().setUp()
        self.create_signing_profile()
        for code in ("110", "111", "112"):
            self.validate_authorization(
                document_code=code, folio_start=300, folio_end=320
            )
        self.root = self._accept(self._issue(self._export()))

    def _issue(self, move):
        return move.with_user(self.dte_manager).issue_dte()

    def _accept(self, document):
        document = document.with_user(self.dte_manager)
        for state, role in REMAINING_STEPS:
            if (state == "queued" and document.preparation_state == "queued") or (
                state == "submitted" and document.fiscal_state != "unknown"
            ):
                continue
            artifact = self.create_artifact(document, role, f"test {state}".encode())
            document.action_record_transition(
                state, f"test_{state}", artifact_id=artifact.id
            )
        self.assertEqual(document.fiscal_state, "accepted")
        return document

    def _note(self, code, amount, correction="3", **values):
        move_type = "out_refund" if code == "112" else "out_invoice"
        note = self.init_invoice(
            move_type,
            partner=self.buyer,
            invoice_date="2026-01-20",
            products=[self.product_a],
            taxes=[],
            currency=self.usd,
        )
        note.invoice_line_ids.write(
            {"quantity": 1, "price_unit": amount, "tax_ids": [(5, 0, 0)]}
        )
        note.write(
            {
                "l10n_latam_document_type_id": self.document_types[code].id,
                "dte_export_target_id": self.root.id,
                "dte_export_correction": correction,
                "dte_export_reason": "Price adjustment",
                "dte_export_rate": 951.10,
                "dte_export_rate_date": "2026-01-20",
                "dte_export_rate_source": "BCCh observed dollar 2026-01-20",
                **values,
            }
        )
        note.action_post()
        return note
