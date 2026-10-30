# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

_logger = logging.getLogger(__name__)


def _column_exists(cr, table, column):
    cr.execute(
        """
        SELECT EXISTS (
            SELECT 1
              FROM information_schema.columns
             WHERE table_schema = current_schema()
               AND table_name = %s
               AND column_name = %s
        )
        """,
        (table, column),
    )
    return cr.fetchone()[0]


def _copy_legacy_reference(cr, source, target, source_type="integer"):
    """Copy a legacy move field without overwriting or deleting its source."""
    if not _column_exists(cr, "account_move", source):
        return 0
    if not _column_exists(cr, "account_move", target):
        return 0
    cr.execute(
        f"""
        UPDATE account_move
           SET {target} = {source}::{source_type}
         WHERE {target} IS NULL
           AND {source} IS NOT NULL
        """
    )
    migrated = cr.rowcount
    cr.execute(
        f"""
        SELECT count(*)
          FROM account_move
         WHERE {source} IS NOT NULL
           AND {target} IS NOT NULL
           AND {target} <> {source}::{source_type}
        """
    )
    conflicts = cr.fetchone()[0]
    if conflicts:
        _logger.warning(
            "Kept %s conflicting values in legacy account_move column %s; "
            "the canonical field %s was not overwritten.",
            conflicts,
            source,
            target,
        )
    return migrated


def _copy_legacy_track_id(cr):
    source = "l10n_cl_sii_send_ident"
    target = "l10n_cl_dte_track_id"
    if not _column_exists(cr, "account_move", source):
        return 0
    if not _column_exists(cr, "account_move", target):
        return 0

    cr.execute(
        """
        SELECT character_maximum_length
          FROM information_schema.columns
         WHERE table_schema = current_schema()
           AND table_name = 'account_move'
           AND column_name = %s
        """,
        (target,),
    )
    max_length = cr.fetchone()[0]
    length_clause = "" if max_length is None else "AND length(source_value) <= %s"
    params = () if max_length is None else (max_length,)
    cr.execute(
        f"""
        UPDATE account_move AS move
           SET {target} = source.source_value
          FROM (
                SELECT id, btrim({source}) AS source_value
                  FROM account_move
               ) AS source
         WHERE move.id = source.id
           AND move.{target} IS NULL
           AND source.source_value <> ''
           {length_clause}
        """,
        params,
    )
    migrated = cr.rowcount
    cr.execute(
        f"""
        SELECT count(*)
          FROM account_move
         WHERE {target} IS NULL
           AND {source} IS NOT NULL
           AND btrim({source}) <> ''
        """
    )
    skipped = cr.fetchone()[0]
    if skipped:
        _logger.warning(
            "Kept %s legacy SII identifiers in their source column because the "
            "destination already has a value or cannot store the full value.",
            skipped,
        )
    cr.execute(
        f"""
        SELECT count(*)
          FROM account_move
         WHERE {target} IS NOT NULL
           AND {source} IS NOT NULL
           AND btrim({source}) <> ''
           AND {target} <> btrim({source})
        """
    )
    conflicts = cr.fetchone()[0]
    if conflicts:
        _logger.warning(
            "Kept %s conflicting legacy SII identifiers; the canonical TrackID "
            "was not overwritten.",
            conflicts,
        )
    return migrated


def _assign_legacy_attachment_companies(cr):
    cr.execute(
        """
        UPDATE ir_attachment AS attachment
           SET company_id = move.company_id
          FROM account_move AS move
         WHERE attachment.company_id IS NULL
           AND attachment.res_model = 'account.move'
           AND attachment.res_id = move.id
           AND move.company_id IS NOT NULL
           AND attachment.id IN (
                move.l10n_cl_dte_attachment_id,
                move.l10n_cl_dte_envelope_attachment_id
           )
        """
    )
    return cr.rowcount


def _conflicting_dte_attachment_companies(cr):
    """Return DTE attachments whose company conflicts with their move."""
    required_columns = [
        ("account_move", "company_id"),
        ("account_move", "l10n_cl_dte_attachment_id"),
        ("account_move", "l10n_cl_dte_envelope_attachment_id"),
        ("ir_attachment", "company_id"),
    ]
    if not all(_column_exists(cr, table, column) for table, column in required_columns):
        return []
    attachment_columns = [
        "l10n_cl_dte_attachment_id",
        "l10n_cl_dte_envelope_attachment_id",
    ]
    attachment_columns.extend(
        column
        for column in ("l10n_cl_dte_file", "l10n_cl_sii_send_file")
        if _column_exists(cr, "account_move", column)
    )
    attachment_ids = ", ".join(f"move.{column}" for column in attachment_columns)
    cr.execute(
        f"""
        SELECT attachment.id, attachment.company_id, move.id, move.company_id
          FROM account_move AS move
          JOIN ir_attachment AS attachment
            ON attachment.res_model = 'account.move'
           AND attachment.res_id = move.id
           AND attachment.id IN ({attachment_ids})
         WHERE attachment.company_id IS NOT NULL
           AND move.company_id IS NOT NULL
           AND attachment.company_id <> move.company_id
         ORDER BY attachment.id
        """
    )
    return cr.fetchall()


def migrate_legacy_account_moves(env):
    """Move source-addon DTE pointers into the new core fields, idempotently.

    Legacy columns and attachments remain intact as migration evidence. Values
    are copied only into empty destinations; conflicts are never overwritten.
    """
    cr = env.cr
    if not _column_exists(cr, "account_move", "id"):
        return {}

    migrated = {
        "dte_attachment": _copy_legacy_reference(
            cr, "l10n_cl_dte_file", "l10n_cl_dte_attachment_id"
        ),
        "envelope_attachment": _copy_legacy_reference(
            cr, "l10n_cl_sii_send_file", "l10n_cl_dte_envelope_attachment_id"
        ),
        "track_id": _copy_legacy_track_id(cr),
        "receiver_status": _copy_legacy_reference(
            cr,
            "l10n_cl_dte_partner_status",
            "l10n_cl_dte_receiver_status",
            source_type="varchar",
        ),
    }
    conflicts = _conflicting_dte_attachment_companies(cr)
    if conflicts:
        attachment_ids = [row[0] for row in conflicts[:20]]
        _logger.error(
            "Refusing legacy DTE migration: %s attachment(s) linked to account.move "
            "belong to a different company. Attachment IDs: %s",
            len(conflicts),
            attachment_ids,
        )
        raise RuntimeError(
            "Legacy DTE attachments belong to a different company; "
            f"review attachment IDs {attachment_ids}."
        )
    migrated["attachment_company"] = _assign_legacy_attachment_companies(cr)
    total = sum(migrated.values())
    if total:
        _logger.info(
            "Preserved legacy SII values on account.move: %s",
            ", ".join(f"{key}={value}" for key, value in migrated.items()),
        )
    return migrated


def post_init_hook(env):
    """Preserve legacy columns when this addon is first installed on an old DB."""
    return migrate_legacy_account_moves(env)
