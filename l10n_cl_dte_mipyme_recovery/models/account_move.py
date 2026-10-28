# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Prevent the regular Chilean EDI post hook during DTE reconstruction."""

from odoo import _, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare

#: Module that owns the DTE emission hook. It is optional: this module must work
#: both with and without it, so it is never declared as a dependency.
DTE_LOCALIZATION = "odoo.addons.l10n_cl_edi"


class AccountMove(models.Model):
    _inherit = "account.move"

    l10n_cl_mipyme_imported = fields.Boolean(
        string="Imported from SII MiPyme",
        copy=False,
        readonly=True,
        help="The DTE was already issued by SII and must not be emitted again.",
    )
    l10n_cl_mipyme_xml_total = fields.Monetary(
        string="Recovered XML total", copy=False, readonly=True
    )

    _L10N_CL_MIPYME_FISCAL_FIELDS = frozenset(
        {
            "company_id",
            "partner_id",
            "move_type",
            "journal_id",
            "invoice_date",
            "l10n_latam_document_type_id",
            "l10n_latam_document_number",
        }
    )

    def write(self, vals):
        imported = self.filtered("l10n_cl_mipyme_imported")
        if imported and self._L10N_CL_MIPYME_FISCAL_FIELDS.intersection(vals):
            raise UserError(
                _(
                    "Las facturas DTE importadas no permiten modificar su identidad"
                    "fiscal."
                )
            )
        return super().write(vals)

    def _post_without_dte(self, soft=True):
        """Run ``_post`` skipping the DTE localization hook, when installed.

        The recovered XML was already issued and signed by the SII, so signing
        and queueing a new DTE must not happen. The owning class is located
        through the MRO, which keeps this module free of the optional import.
        """
        owners = [
            klass
            for klass in type(self).__mro__
            if klass.__module__.startswith(DTE_LOCALIZATION)
        ]
        if owners:
            return super(owners[-1], self)._post(soft=soft)
        return super()._post(soft=soft)

    def _post(self, soft=True):
        imported = self.filtered("l10n_cl_mipyme_imported")
        if imported:
            for move in imported:
                if float_compare(
                    move.amount_total,
                    move.l10n_cl_mipyme_xml_total,
                    precision_rounding=move.currency_id.rounding,
                ):
                    raise ValidationError(
                        _(
                            "El total de la factura no coincide con el total del XML"
                            "recuperado."
                        )
                    )
            posted = imported._post_without_dte(soft=soft)
            values = {
                "l10n_cl_dte_status": "accepted",
                "l10n_cl_dte_acceptation_status": "accepted",
            }
            values = {
                key: value for key, value in values.items() if key in imported._fields
            }
            if values:
                imported.sudo().write(values)
            normal = self - imported
            if normal:
                return posted | normal._post(soft=soft)
            return posted
        if self.env.context.get("l10n_cl_mipyme_recovery_import"):
            return self._post_without_dte(soft=soft)
        return super()._post(soft=soft)
