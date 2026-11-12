# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, models
from odoo.exceptions import ValidationError

DOCUMENT_MODEL = "l10n_cl.electronic.document"


class DteTypedSourceRegistry(models.AbstractModel):
    """Map each DTE source model to its exclusive document relation.

    Addons that issue DTE extend ``_dte_source_relations`` and call ``super()``.
    Because the registry is built from the model inheritance chain, it is
    specific to each database and an uninstalled addon never registers.
    Every method is private so that it cannot be reached through RPC.
    """

    _name = "l10n_cl.dte.source.registry"
    _description = "Typed DTE source registry"

    @api.model
    def _dte_source_relations(self):
        """Return ``{source model: relation field on the electronic document}``."""
        return {}

    @api.model
    def _dte_source_relation(self, source_model):
        """Return the validated relation field for ``source_model``."""
        relations = self._dte_source_relations()
        field_name = relations.get(source_model)
        if not field_name:
            raise ValidationError(
                _("%s is not a registered source of electronic tax documents.")
                % source_model
            )
        self._check_relation(source_model, field_name)
        return field_name

    @api.model
    def _dte_source_relation_fields(self):
        """Return every registered relation field, rejecting shared fields."""
        relations = self._dte_source_relations()
        fields_ = frozenset(relations.values())
        if len(fields_) != len(relations):
            raise ValidationError(
                _("Two source models cannot share one electronic document relation.")
            )
        for source_model, field_name in relations.items():
            self._check_relation(source_model, field_name)
        return fields_

    @api.model
    def _check_relation(self, source_model, field_name):
        field = self.env[DOCUMENT_MODEL]._fields.get(field_name)
        if (
            field is None
            or field.type != "many2one"
            or field.comodel_name != source_model
        ):
            raise ValidationError(
                _(
                    "The electronic document field %(field)s is not a Many2one "
                    "relation to %(model)s.",
                    field=field_name,
                    model=source_model,
                )
            )
