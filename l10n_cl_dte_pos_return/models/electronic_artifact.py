# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.exceptions import UserError


class ElectronicArtifact(models.Model):
    _inherit = "l10n_cl.electronic.artifact"

    role = fields.Selection(
        selection_add=[("pos_line_mapping", "POS Line Mapping")],
        ondelete={
            "pos_line_mapping": lambda records: (
                records._uninstall_pos_line_mapping_role()
            )
        },
    )

    def _uninstall_pos_line_mapping_role(self):
        raise UserError(
            _(
                "Preserve immutable POS fiscal mapping evidence before "
                "removing this addon."
            )
        )
