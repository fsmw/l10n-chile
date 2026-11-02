# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).


def migrate(cr, version):
    cr.execute("SELECT to_regclass('l10n_cl_electronic_document')")
    if not cr.fetchone()[0]:
        return
    cr.execute(
        "ALTER TABLE l10n_cl_electronic_document "
        "ADD COLUMN IF NOT EXISTS sii_environment varchar"
    )
    cr.execute("SELECT to_regclass('l10n_cl_electronic_submission')")
    if cr.fetchone()[0]:
        cr.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'l10n_cl_electronic_submission' "
            "AND column_name = 'environment'"
        )
        if cr.fetchone():
            cr.execute(
                "UPDATE l10n_cl_electronic_document AS document "
                "SET sii_environment = submission.environment "
                "FROM l10n_cl_electronic_submission AS submission "
                "WHERE submission.document_id = document.id "
                "AND document.sii_environment IS NULL"
            )
    cr.execute("SELECT to_regclass('res_company')")
    if cr.fetchone()[0]:
        cr.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'res_company' "
            "AND column_name = 'dte_sii_environment'"
        )
        has_company_mode = bool(cr.fetchone())
        if has_company_mode:
            cr.execute(
                "UPDATE l10n_cl_electronic_document AS document "
                "SET sii_environment = company.dte_sii_environment "
                "FROM res_company AS company "
                "WHERE company.id = document.company_id "
                "AND document.sii_environment IS NULL"
            )
    cr.execute(
        "UPDATE l10n_cl_electronic_document "
        "SET sii_environment = 'certification' "
        "WHERE sii_environment IS NULL"
    )
