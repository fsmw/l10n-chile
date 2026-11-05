# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError

from odoo.addons.l10n_cl_dte_note.services.note_reference_service import (
    NoteReferenceService,
)


def _authorized_document(document):
    """Read evidence internally without granting cashiers raw artifact access."""
    document.ensure_one()
    document.check_access("read")
    if (
        not document.env.user.has_group("l10n_cl_dte_core.group_dte_user")
        or document.company_id not in document.env.companies
    ):
        raise AccessError(
            document.env._("The POS operator cannot read this fiscal source.")
        )
    return document.sudo()


def read_snapshot(document):
    document = _authorized_document(document)
    return NoteReferenceService(document.env).read_original_snapshot(document)


def available_amounts(document):
    document = _authorized_document(document)
    return NoteReferenceService(document.env).calculate_available_amounts(document)
