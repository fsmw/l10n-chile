# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).


def migrate(cr, version):
    cr.execute("SELECT to_regclass('l10n_cl_dte_certification_set')")
    if not cr.fetchone()[0]:
        return
    cr.execute(
        "ALTER TABLE l10n_cl_dte_certification_set "
        "DROP CONSTRAINT IF EXISTS "
        "l10n_cl_dte_certification_set_company_source_attention_uniq"
    )
