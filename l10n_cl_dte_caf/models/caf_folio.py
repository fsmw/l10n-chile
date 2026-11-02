# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from psycopg2 import IntegrityError, errors

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class CafFolio(models.Model):
    _name = "l10n_cl.caf.folio"
    _description = "Reserved DTE Folio"
    _order = "number"
    _check_company_auto = True
    _sql_constraints = [
        (
            "folio_authorization_number_uniq",
            "unique(authorization_id, number)",
            "This folio is already recorded for the CAF.",
        ),
        (
            "folio_company_type_number_uniq",
            "unique(company_id, document_code, number)",
            "This folio is already recorded for the company and document type.",
        ),
        (
            "folio_document_uniq",
            "unique(document_id)",
            "A document can reserve only one folio.",
        ),
        (
            "folio_reservation_key_uniq",
            "unique(reservation_key)",
            "This reservation key has already been used.",
        ),
    ]

    authorization_id = fields.Many2one(
        "l10n_cl.caf.authorization", required=True, index=True, ondelete="restrict"
    )
    company_id = fields.Many2one(
        related="authorization_id.company_id", store=True, index=True
    )
    document_code = fields.Char(
        related="authorization_id.document_code", store=True, index=True
    )
    number = fields.Integer(required=True, index=True, copy=False)
    document_id = fields.Many2one(
        "l10n_cl.electronic.document",
        required=True,
        index=True,
        ondelete="restrict",
        copy=False,
        check_company=True,
    )
    reservation_key = fields.Char(required=True, index=True, copy=False)
    state = fields.Selection(
        [("reserved", "Reserved"), ("issued", "Issued"), ("void", "Voided")],
        required=True,
        default="reserved",
        index=True,
        copy=False,
    )
    reserved_at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    reserved_by = fields.Many2one(
        "res.users", default=lambda self: self.env.user, readonly=True
    )
    issued_at = fields.Datetime(readonly=True, copy=False)
    voided_at = fields.Datetime(readonly=True, copy=False)
    void_reason = fields.Text(readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super
        raise AccessError(_("Folios can only be created by the reservation action."))

    def write(self, vals):  # pylint: disable=method-required-super
        raise UserError(_("Reserved folio records are immutable."))

    def unlink(self):  # pylint: disable=method-required-super
        raise AccessError(_("A reserved folio must remain in the fiscal ledger."))

    @api.constrains("number", "authorization_id")
    def _check_folio_range(self):
        for folio in self:
            if (
                folio.number <= 0
                or folio.number < folio.authorization_id.folio_start
                or folio.number > folio.authorization_id.folio_end
            ):
                raise ValidationError(_("The folio must be inside its CAF range."))

    def action_consume(self):
        self.ensure_one()
        self._check_manager()
        if self.state == "issued":
            return True
        if self.state != "reserved" or self.document_id.preparation_state not in {
            "signed",
            "queued",
        }:
            raise UserError(
                _("A folio can be consumed only after its DTE has been signed.")
            )
        with self.env.cr.savepoint():
            super().write({"state": "issued", "issued_at": fields.Datetime.now()})
        return True

    def action_void(self, reason):
        self.ensure_one()
        self._check_manager()
        if self.state != "reserved":
            raise UserError(_("Only a reserved folio can be voided."))
        if not isinstance(reason, str) or not reason.strip():
            raise ValidationError(_("A reason is required to void a folio."))
        with self.env.cr.savepoint():
            super().write(
                {
                    "state": "void",
                    "voided_at": fields.Datetime.now(),
                    "void_reason": reason.strip()[:4000],
                }
            )
        return True

    @api.model
    def _create_reserved(self, values):
        self._check_manager()
        return super().create(values)

    def _check_manager(self):
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(_("Only a DTE manager can reserve or consume folios."))


class CafAuthorization(models.Model):
    _inherit = "l10n_cl.caf.authorization"

    def action_reserve_folio(self, document, reservation_key):
        """Reserve the next folio without reusing a number or duplicating work."""
        self.ensure_one()
        self._check_manager()
        document.ensure_one()
        reservation_key = (reservation_key or "").strip()
        if not reservation_key or len(reservation_key) > 256:
            raise ValidationError(
                _("A reservation key between 1 and 256 characters is required.")
            )
        if document.company_id != self.company_id:
            raise ValidationError(_("The CAF and DTE must belong to the same company."))
        self._check_document_environment(document)
        if document.document_code != self.document_code:
            raise ValidationError(_("The CAF and DTE document types do not match."))

        folio_model = self.env["l10n_cl.caf.folio"]
        try:
            with self.env.cr.savepoint():
                self._lock_range()
                existing_by_key = folio_model.search(
                    [("reservation_key", "=", reservation_key)], limit=1
                )
                if existing_by_key:
                    if (
                        existing_by_key.document_id != document
                        or existing_by_key.authorization_id != self
                    ):
                        raise ValidationError(
                            _("The reservation key is bound to another DTE or CAF.")
                        )
                    if (
                        document.preparation_state != "numbered"
                        or document.folio != existing_by_key.number
                    ):
                        raise ValidationError(
                            _("The existing reservation does not match the DTE state.")
                        )
                    return existing_by_key
                existing_by_document = folio_model.search(
                    [("document_id", "=", document.id)], limit=1
                )
                if existing_by_document:
                    if (
                        existing_by_document.reservation_key == reservation_key
                        and existing_by_document.authorization_id == self
                    ):
                        if (
                            document.preparation_state != "numbered"
                            or document.folio != existing_by_document.number
                        ):
                            raise ValidationError(
                                _("The reservation does not match the DTE state.")
                            )
                        return existing_by_document
                    raise ValidationError(
                        _("This DTE already has a folio reservation.")
                    )
                if self.state != "validated":
                    raise UserError(_("Only a validated CAF can reserve folios."))
                self._check_caf_integrity()
                if document.preparation_state != "draft":
                    raise UserError(_("A folio can only be reserved for a draft DTE."))
                if self.date_to and self.date_to < fields.Date.context_today(self):
                    raise UserError(_("An expired CAF cannot reserve a folio."))
                number = self._next_available_folio()
                evidence = self.env["l10n_cl.electronic.artifact"].create(
                    {
                        "document_id": document.id,
                        "role": "caf_source",
                        "content": self.caf_file,
                        "mimetype": "application/xml",
                        "producer_version": "l10n_cl_dte_caf/18.0",
                    }
                )
                document.action_record_transition(
                    "authorized", "caf_validated", artifact_id=evidence.id
                )
                folio = folio_model._create_reserved(
                    {
                        "authorization_id": self.id,
                        "number": number,
                        "document_id": document.id,
                        "reservation_key": reservation_key,
                    }
                )
                document.action_record_transition(
                    "numbered",
                    "folio_reserved",
                    artifact_id=evidence.id,
                    folio_number=number,
                )
                if number == self.folio_end:
                    self._write_internal({"state": "exhausted"})
                return folio
        except IntegrityError as error:
            concurrency_constraints = {
                "l10n_cl_caf_folio_folio_authorization_number_uniq",
                "l10n_cl_caf_folio_folio_company_type_number_uniq",
            }
            if error.diag.constraint_name in concurrency_constraints:
                raise errors.SerializationFailure(
                    "Concurrent CAF reservation changed the next available folio; "
                    "retry the complete transaction with the same reservation key."
                ) from error
            raise

    def _check_document_environment(self, document):
        self.ensure_one()
        if document.sii_environment != self.sii_environment:
            raise ValidationError(
                _("The CAF environment must match the DTE before reserving a folio.")
            )

    def _next_available_folio(self):
        self.ensure_one()
        self.env.cr.execute(
            """SELECT number FROM l10n_cl_caf_folio
               WHERE company_id = %s AND document_code = %s
                 AND number BETWEEN %s AND %s
               ORDER BY number""",
            [self.company_id.id, self.document_code, self.folio_start, self.folio_end],
        )
        candidate = self.folio_start
        for (reserved_number,) in self.env.cr.fetchall():
            if reserved_number == candidate:
                candidate += 1
            elif reserved_number > candidate:
                break
        if candidate > self.folio_end:
            raise UserError(_("This CAF has no available folios."))
        return candidate
