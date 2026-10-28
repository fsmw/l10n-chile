# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import base64

from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class RcvDraftWizard(models.TransientModel):
    _name = "l10n.cl.rcv.draft.wizard"
    _description = "Create reviewed vendor bill draft from RCV"

    document_id = fields.Many2one("l10n.cl.rcv.document", required=True, readonly=True)
    company_id = fields.Many2one(related="document_id.company_id")
    currency_id = fields.Many2one(related="document_id.currency_id")
    journal_id = fields.Many2one("account.journal", compute="_compute_journal_id")
    partner_candidate_ids = fields.Many2many(
        "res.partner", compute="_compute_partner_candidates"
    )
    partner_id = fields.Many2one(
        "res.partner", domain="[('id', 'in', partner_candidate_ids)]"
    )
    new_partner_name = fields.Char(string="Nombre del nuevo proveedor")
    xml_file = fields.Binary()
    xml_filename = fields.Char()
    line_ids = fields.One2many(
        "l10n.cl.rcv.draft.wizard.line", "wizard_id", string="Líneas de factura"
    )
    draft_untaxed_amount = fields.Monetary(
        string="Neto propuesto",
        compute="_compute_draft_totals",
        currency_field="currency_id",
    )
    draft_tax_amount = fields.Monetary(
        string="Impuestos propuestos",
        compute="_compute_draft_totals",
        currency_field="currency_id",
    )
    draft_total_amount = fields.Monetary(
        string="Total propuesto",
        compute="_compute_draft_totals",
        currency_field="currency_id",
    )
    rcv_net_amount = fields.Monetary(
        string="Neto informado por el RCV", related="document_id.net_amount"
    )
    rcv_exempt_amount = fields.Monetary(
        string="Exento informado por el RCV", related="document_id.exempt_amount"
    )
    rcv_vat_amount = fields.Monetary(
        string="IVA informado por el RCV", related="document_id.vat_amount"
    )
    rcv_total_amount = fields.Monetary(
        string="Total informado por el RCV", related="document_id.total_amount"
    )

    @api.depends("company_id")
    def _compute_journal_id(self):
        for wizard in self:
            if not wizard.company_id:
                wizard.journal_id = False
                continue
            move = (
                self.env["account.move"]
                .with_company(wizard.company_id)
                .new(
                    {
                        "move_type": "in_invoice",
                        "company_id": wizard.company_id.id,
                    }
                )
            )
            wizard.journal_id = move._search_default_journal()

    @api.depends(
        "line_ids.amount_untaxed", "line_ids.amount_tax", "line_ids.amount_total"
    )
    def _compute_draft_totals(self):
        for wizard in self:
            wizard.draft_untaxed_amount = sum(wizard.line_ids.mapped("amount_untaxed"))
            wizard.draft_tax_amount = sum(wizard.line_ids.mapped("amount_tax"))
            wizard.draft_total_amount = sum(wizard.line_ids.mapped("amount_total"))

    def _default_purchase_account(self, partner, product=None):
        self.ensure_one()
        company = self.company_id
        fiscal_position = (
            self.env["account.fiscal.position"]
            .with_company(company)
            ._get_fiscal_position(partner)
        )
        if product:
            accounts = product.with_company(
                company
            ).product_tmpl_id.get_product_accounts(fiscal_pos=fiscal_position)
            if accounts.get("expense"):
                account = self._account_for_company(accounts["expense"])
                if account:
                    return account
        account_model = self.env["account.account"].with_company(company)
        frequent = account_model._get_most_frequent_account_for_partner(
            company.id,
            partner.id,
            move_type="in_invoice",
            journal_id=self.journal_id.id if self.journal_id else False,
        )
        account = self._account_for_company(frequent)
        if account:
            return account
        return self._account_for_company(
            self.journal_id.default_account_id if self.journal_id else False
        )

    def _account_for_company(self, account):
        self.ensure_one()
        account_model = self.env["account.account"].with_company(self.company_id)
        if isinstance(account, int) and not isinstance(account, bool):
            account = account_model.browse(account)
        if getattr(account, "_name", False) != "account.account":
            return account_model
        account = account.exists()
        if len(account) != 1 or self.company_id not in account.company_ids:
            return account_model
        return account

    def _find_rcv_component_tax(self, tag_xmlid):
        self.ensure_one()
        return self.env["l10n.cl.rcv.fiscal.evidence"].find_component_tax(
            self.company_id, tag_xmlid
        )

    def action_autocomplete_from_rcv(self):
        self.ensure_one()
        if (
            self.document_id.operation != "purchase"
            or self.document_id.reconciliation_state != "missing"
        ):
            raise UserError(
                _("Solo se puede completar una compra faltante en Odoo desde el RCV.")
            )
        return self._autocomplete_from_rcv()

    def _autocomplete_from_rcv(self):
        """Build summarized RCV lines for a user or the guarded automation flow."""
        self.ensure_one()
        doc = self.document_id
        if doc.operation != "purchase":
            raise UserError(_("Solo se pueden completar compras desde el RCV."))
        if self.xml_file:
            raise UserError(
                _("Con XML cargado, use Crear borrador para importar el detalle DTE.")
            )
        if self.line_ids:
            raise UserError(
                _("Elimine las líneas existentes antes de autocompletar desde el RCV.")
            )
        partner = self.partner_id
        if not partner and len(self.partner_candidate_ids) == 1:
            partner = self.partner_candidate_ids
        account = (
            self._default_purchase_account(partner)
            if partner
            else self.journal_id.default_account_id
        )
        lines = []
        if doc.net_amount:
            components = [
                (
                    "vat_amount",
                    doc.vat_amount,
                    "l10n_cl.tag_cl_purchase_mnt_iva",
                    _("Neto afecto según RCV"),
                ),
                (
                    "vat_not_recoverable",
                    doc.vat_not_recoverable,
                    "l10n_cl.tag_cl_purchase_mnt_iva_no_rec",
                    _("Neto con IVA no recuperable según RCV"),
                ),
                (
                    "common_vat",
                    doc.common_vat,
                    "l10n_cl.tag_cl_purchase_mnt_iva_uso_comun",
                    _("Neto con IVA de uso común según RCV"),
                ),
            ]
            active_components = [item for item in components if item[1]]
            if len(active_components) == 1:
                component, amount, tag, description = active_components[0]
                tax = self._find_rcv_component_tax(tag)
                lines.append(
                    self._summary_line_values(
                        description,
                        doc.net_amount,
                        account,
                        tax,
                        "taxable" if tax else "unresolved",
                        ""
                        if tax
                        else _(
                            "No hay un impuesto chileno único para este componente."
                        ),
                        component,
                    )
                )
            elif active_components:
                component_lines = []
                mapped = True
                for component, amount, tag, description in active_components:
                    tax = self._find_rcv_component_tax(tag)
                    if not tax:
                        mapped = False
                        break
                    base = self.currency_id.round(amount * 100.0 / tax.amount)
                    component_lines.append(
                        self._summary_line_values(
                            description,
                            base,
                            account,
                            tax,
                            "taxable",
                            "",
                            component,
                        )
                    )
                base_total = sum(line["price_unit"] for line in component_lines)
                if not mapped or not self.currency_id.is_zero(
                    base_total - doc.net_amount
                ):
                    lines.append(
                        self._summary_line_values(
                            _("Neto afecto según RCV"),
                            doc.net_amount,
                            account,
                            self.env["account.tax"],
                            "unresolved",
                            _(
                                "No fue posible distribuir el neto entre los "
                                "componentes "
                                "IVA del RCV."
                            ),
                            "net",
                        )
                    )
                else:
                    lines.extend(component_lines)
            else:
                lines.append(
                    self._summary_line_values(
                        _("Neto según RCV"),
                        doc.net_amount,
                        account,
                        self.env["account.tax"],
                        "unresolved",
                        _(
                            "El RCV no identifica el tratamiento tributario del "
                            "monto neto."
                        ),
                        "net",
                    )
                )
        if doc.exempt_amount:
            lines.append(
                self._summary_line_values(
                    _("Monto exento según RCV"),
                    doc.exempt_amount,
                    account,
                    self.env["account.tax"],
                    "exempt",
                    "",
                    "exempt",
                )
            )
        if not lines:
            raise UserError(_("El RCV no contiene importes para completar."))
        self.line_ids = [(0, 0, values) for values in lines]
        return True

    def _summary_line_values(
        self, description, base, account, taxes, treatment, note, component
    ):
        return {
            "name": description,
            "quantity": 1,
            "price_unit": base,
            "account_id": account.id if account else False,
            "tax_ids": [(6, 0, taxes.ids)],
            "tax_treatment": treatment,
            "tax_mapping_note": note,
            "summary_component": component,
        }

    @api.depends("document_id.issuer_vat")
    def _compute_partner_candidates(self):
        matcher = self.env["l10n.cl.rcv.matcher"]
        for wizard in self:
            wizard.partner_candidate_ids = matcher._partners_by_vat(
                wizard.document_id.issuer_vat
            )

    @api.model_create_multi
    def create(self, vals_list):
        wizards = super().create(vals_list)
        matcher = self.env["l10n.cl.rcv.matcher"]
        for wizard in wizards:
            candidates = matcher._partners_by_vat(wizard.document_id.issuer_vat)
            if len(candidates) == 1 and not wizard.partner_id:
                wizard.partner_id = candidates
            elif not candidates and not wizard.partner_id:
                wizard.new_partner_name = wizard.document_id.issuer_name
        return wizards

    @api.onchange("document_id")
    def _onchange_document_id_partner(self):
        for wizard in self:
            candidates = self.env["l10n.cl.rcv.matcher"]._partners_by_vat(
                wizard.document_id.issuer_vat
            )
            wizard.partner_id = candidates if len(candidates) == 1 else False
            wizard.new_partner_name = (
                wizard.document_id.issuer_name if not candidates else False
            )

    def _resolve_or_create_partner(self):
        self.ensure_one()
        doc = self.document_id
        matcher = self.env["l10n.cl.rcv.matcher"]
        candidates = matcher._partners_by_vat(doc.issuer_vat)
        if len(candidates) > 1:
            if self.partner_id not in candidates:
                raise UserError(
                    _("Hay varios proveedores con este RUT. Seleccione el correcto.")
                )
            return self.partner_id
        if len(candidates) == 1:
            if self.partner_id and self.partner_id != candidates:
                raise UserError(
                    _("El proveedor seleccionado no coincide con el RUT del RCV.")
                )
            self.partner_id = candidates
            return candidates
        if self.partner_id:
            raise UserError(
                _("El proveedor seleccionado no coincide con el RUT del RCV.")
            )
        partner_name = (self.new_partner_name or doc.issuer_name or "").strip()
        if not partner_name:
            raise UserError(
                _("Ingrese el nombre del proveedor antes de crear el borrador.")
            )
        partner = self.env["res.partner"].create(
            {
                "name": partner_name,
                "vat": matcher._normalize_vat(doc.issuer_vat),
                "company_type": "company",
                "supplier_rank": 1,
            }
        )
        self.partner_id = partner
        return partner

    def action_open(self):
        self.ensure_one()
        if not self.xml_file and not self.line_ids:
            self.action_autocomplete_from_rcv()
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_create_draft(self):
        self.ensure_one()
        doc = self.document_id
        if doc.operation != "purchase" or doc.reconciliation_state != "missing":
            raise UserError(
                _("Solo se puede crear un borrador para una compra faltante en Odoo.")
            )
        return self._create_draft()

    def _create_draft(self):
        """Create a draft internally after its caller applies the right guards."""
        self.ensure_one()
        doc = self.document_id
        if doc.operation != "purchase":
            raise UserError(
                _("Solo se puede crear un borrador para una compra del RCV.")
            )
        if self.env["l10n.cl.rcv.matcher"]._candidate_moves(doc):
            raise UserError(
                _("Ya existe una factura con la identidad fiscal del documento.")
            )
        if self.xml_file:
            return self._create_from_xml()
        if not self.line_ids:
            raise UserError(
                _(
                    "Agregue y revise líneas, cuentas e impuestos antes de crear "
                    "el borrador."
                )
            )
        if not self.xml_file and self.line_ids.filtered(
            lambda line: (
                line.summary_component
                in ("vat_amount", "vat_not_recoverable", "common_vat")
                and not line.tax_ids
            )
        ):
            raise UserError(
                _(
                    "Seleccione el impuesto correspondiente en cada línea con IVA "
                    "informado por el RCV."
                )
            )
        if not self.xml_file and self.line_ids.filtered(
            lambda line: line.tax_treatment == "exempt" and line.tax_ids
        ):
            raise UserError(
                _("Quite los impuestos de las líneas clasificadas como exentas.")
            )
        document_type = self.env["l10n_latam.document.type"].search(
            [("code", "=", doc.document_type_code), ("country_id.code", "=", "CL")],
            limit=1,
        )
        if not document_type:
            raise UserError(
                _("No existe el tipo de documento DTE %s en Odoo.")
                % doc.document_type_code
            )
        partner = self._resolve_or_create_partner()
        move_type = (
            "in_refund"
            if document_type.internal_type == "credit_note"
            else "in_invoice"
        )
        move = self.env["account.move"].create(
            {
                "move_type": move_type,
                "company_id": doc.company_id.id,
                "partner_id": partner.id,
                "journal_id": self.journal_id.id,
                "invoice_date": doc.document_date,
                "l10n_latam_document_type_id": document_type.id,
                "l10n_latam_document_number": doc.folio,
                "l10n_cl_rcv_document_id": doc.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": line.name,
                            "product_id": line.product_id.id,
                            "quantity": line.quantity,
                            "product_uom_id": line.product_uom_id.id,
                            "price_unit": line.price_unit,
                            "account_id": line.account_id.id
                            or self._default_purchase_account(
                                partner, line.product_id
                            ).id,
                            "tax_ids": [(6, 0, line.tax_ids.ids)],
                            "analytic_distribution": line.analytic_distribution
                            or False,
                        },
                    )
                    for line in self.line_ids
                ],
            }
        )
        self.env["l10n.cl.rcv.match.log"].create(
            {
                "document_id": doc.id,
                "move_id": move.id,
                "reason": _("Borrador creado después de confirmar la revisión RCV."),
            }
        )
        self.env["l10n.cl.rcv.matcher"]._reconcile(doc)
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": move.id,
            "view_mode": "form",
            "target": "current",
        }

    def _create_from_xml(self):
        doc = self.document_id
        try:
            root = etree.fromstring(base64.b64decode(self.xml_file))
        except (ValueError, etree.XMLSyntaxError) as exc:
            raise UserError(_("El archivo no es un XML DTE válido.")) from exc
        dtes = root.xpath("//ns0:DTE", namespaces={"ns0": "http://www.sii.cl/SiiDte"})
        if len(dtes) != 1:
            raise UserError(_("El XML debe contener exactamente un DTE."))
        self._validate_xml_identity(dtes[0])
        document_type = self.env["l10n_latam.document.type"].search(
            [("code", "=", doc.document_type_code), ("country_id.code", "=", "CL")],
            limit=1,
        )
        if not document_type:
            raise UserError(
                _("No existe el tipo de documento DTE %s en Odoo.")
                % doc.document_type_code
            )
        partner = self._resolve_or_create_partner()
        placeholder = self.env["account.move"].create(
            {
                "move_type": (
                    "in_refund"
                    if document_type.internal_type == "credit_note"
                    else "in_invoice"
                ),
                "company_id": doc.company_id.id,
                "partner_id": partner.id,
            }
        )
        moves = placeholder._l10n_cl_create_document_from_attachment(
            base64.b64decode(self.xml_file),
            self.xml_filename or "DTE.xml",
            doc.company_id,
            placeholder,
        )
        move = next(
            (
                candidate
                for candidate in moves
                if candidate.l10n_latam_document_number == doc.folio
            ),
            False,
        )
        if not move:
            placeholder.unlink()
            raise UserError(_("El importador DTE no creó el borrador esperado."))
        if placeholder.exists() and placeholder != move:
            placeholder.unlink()
        self.env["l10n.cl.rcv.match.log"].create(
            {
                "document_id": doc.id,
                "move_id": move.id,
                "reason": _(
                    "Borrador importado desde XML DTE tras confirmar la revisión RCV."
                ),
            }
        )
        self.env["l10n.cl.rcv.matcher"]._reconcile(doc)
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": move.id,
            "view_mode": "form",
            "target": "current",
        }

    def _dte_text(self, dte, name):
        """Read one DTE field without depending on the EDI localization."""
        nodes = dte.xpath(".//*[local-name()=$name]", name=name)
        return (nodes[0].text or "").strip() if nodes else ""

    def _validate_xml_identity(self, dte):
        doc = self.document_id
        folio = self._dte_text(dte, "Folio")
        type_code = self._dte_text(dte, "TipoDTE")
        issuer_vat = self._dte_text(dte, "RUTEmisor")
        matcher = self.env["l10n.cl.rcv.matcher"]
        if (
            str(folio) != doc.folio
            or str(type_code) != doc.document_type_code
            or matcher._normalize_vat(issuer_vat)
            != matcher._normalize_vat(doc.issuer_vat)
        ):
            raise UserError(
                _("El XML no corresponde al RUT, tipo y folio del registro RCV.")
            )
        return True


class RcvDraftWizardLine(models.TransientModel):
    _name = "l10n.cl.rcv.draft.wizard.line"
    _inherit = ["analytic.mixin"]
    _description = "Reviewed accounting line for RCV draft"
    _order = "sequence, id"

    wizard_id = fields.Many2one(
        "l10n.cl.rcv.draft.wizard", required=True, ondelete="cascade"
    )
    sequence = fields.Integer(string="Secuencia", default=10)
    product_id = fields.Many2one(
        "product.product",
        string="Producto",
        domain="[('purchase_ok', '=', True)]",
    )
    name = fields.Char(string="Descripción", required=True)
    quantity = fields.Float(string="Cantidad", required=True, default=1)
    price_unit = fields.Monetary(
        string="Precio unitario", required=True, currency_field="currency_id"
    )
    currency_id = fields.Many2one(related="wizard_id.company_id.currency_id")
    product_uom_id = fields.Many2one("uom.uom", string="Unidad de medida")
    product_uom_category_id = fields.Many2one(related="product_id.uom_id.category_id")
    account_id = fields.Many2one(
        "account.account",
        string="Cuenta",
        domain="[('company_ids', 'in', company_id)]",
    )
    company_id = fields.Many2one(related="wizard_id.company_id")
    tax_ids = fields.Many2many(
        "account.tax",
        string="Impuestos",
        domain="[('company_id', '=', company_id)]",
    )
    tax_treatment = fields.Selection(
        [
            ("taxable", "Afecto"),
            ("exempt", "Exento"),
            ("unresolved", "Requiere clasificación"),
        ],
        string="Tratamiento tributario",
        default="taxable",
        required=True,
    )
    tax_mapping_note = fields.Char(string="Observación de impuestos")
    summary_component = fields.Selection(
        [
            ("vat_amount", "IVA recuperable"),
            ("vat_not_recoverable", "IVA no recuperable"),
            ("common_vat", "IVA de uso común"),
            ("exempt", "Exento"),
            ("net", "Neto sin clasificación"),
        ],
        readonly=True,
    )
    amount_untaxed = fields.Monetary(
        compute="_compute_amounts", currency_field="currency_id"
    )
    amount_tax = fields.Monetary(
        compute="_compute_amounts", currency_field="currency_id"
    )
    amount_total = fields.Monetary(
        compute="_compute_amounts", currency_field="currency_id"
    )

    @api.depends(
        "quantity", "price_unit", "tax_ids", "product_id", "wizard_id.partner_id"
    )
    def _compute_amounts(self):
        for line in self:
            taxes = line.tax_ids.compute_all(
                line.price_unit,
                currency=line.currency_id,
                quantity=line.quantity,
                product=line.product_id,
                partner=line.wizard_id.partner_id,
            )
            line.amount_untaxed = taxes["total_excluded"]
            line.amount_total = taxes["total_included"]
            line.amount_tax = line.amount_total - line.amount_untaxed

    @api.onchange("product_id")
    def _onchange_product_id(self):
        for line in self.filtered("product_id"):
            wizard = line.wizard_id
            product = line.product_id.with_company(wizard.company_id)
            fiscal_position = (
                self.env["account.fiscal.position"]
                .with_company(wizard.company_id)
                ._get_fiscal_position(wizard.partner_id)
            )
            if not line.name:
                line.name = product.display_name
            line.product_uom_id = product.uom_id
            line.price_unit = product.standard_price
            company = wizard.company_id
            taxes = product.supplier_taxes_id.filtered(
                lambda tax, company=company: tax.company_id == company
            )
            line.tax_ids = fiscal_position.map_tax(taxes) if fiscal_position else taxes
            line.account_id = wizard._default_purchase_account(
                wizard.partner_id, product
            )

    @api.onchange("wizard_id", "account_id")
    def _onchange_default_account(self):
        for line in self.filtered(lambda row: not row.account_id and row.wizard_id):
            partner = line.wizard_id.partner_id
            line.account_id = line.wizard_id._default_purchase_account(partner)

    @api.onchange("tax_ids")
    def _onchange_tax_ids(self):
        for line in self:
            if line.tax_ids and line.tax_treatment == "unresolved":
                line.tax_treatment = "taxable"
                line.tax_mapping_note = False
