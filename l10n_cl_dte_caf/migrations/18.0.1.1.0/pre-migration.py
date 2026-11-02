# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).


def migrate(cr, version):
    cr.execute("SELECT to_regclass('l10n_cl_caf_authorization')")
    if not cr.fetchone()[0]:
        return
    cr.execute(
        "ALTER TABLE l10n_cl_caf_authorization "
        "ADD COLUMN IF NOT EXISTS sii_environment varchar"
    )
    cr.execute("SELECT to_regclass('l10n_cl_caf_folio')")
    has_folios = bool(cr.fetchone()[0])
    cr.execute("SELECT to_regclass('l10n_cl_electronic_document')")
    has_documents = bool(cr.fetchone()[0])
    if has_folios and has_documents:
        cr.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'l10n_cl_electronic_document' "
            "AND column_name = 'sii_environment'"
        )
        if cr.fetchone():
            cr.execute(
                "SELECT caf_auth.id "
                "FROM l10n_cl_caf_authorization AS caf_auth "
                "JOIN l10n_cl_caf_folio AS folio "
                "ON folio.authorization_id = caf_auth.id "
                "JOIN l10n_cl_electronic_document AS document "
                "ON document.id = folio.document_id "
                "GROUP BY caf_auth.id "
                "HAVING COUNT(DISTINCT document.sii_environment) > 1"
            )
            ambiguous = [row[0] for row in cr.fetchall()]
            if ambiguous:
                caf_ids = ", ".join(map(str, ambiguous))
                raise RuntimeError(
                    "Cannot migrate CAF environment: CAF records are used by DTEs "
                    f"from multiple SII modes (CAF IDs: {caf_ids}). "
                    "Split or resolve them before upgrading."
                )
            cr.execute(
                "UPDATE l10n_cl_caf_authorization AS caf_auth "
                "SET sii_environment = modes.sii_environment "
                "FROM (SELECT folio.authorization_id, "
                "MIN(document.sii_environment) AS sii_environment "
                "FROM l10n_cl_caf_folio AS folio "
                "JOIN l10n_cl_electronic_document AS document "
                "ON document.id = folio.document_id "
                "GROUP BY folio.authorization_id) AS modes "
                "WHERE modes.authorization_id = caf_auth.id "
                "AND caf_auth.sii_environment IS NULL"
            )
    cr.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'res_company' "
        "AND column_name = 'dte_sii_environment'"
    )
    if cr.fetchone():
        cr.execute(
            "UPDATE l10n_cl_caf_authorization AS caf_auth "
            "SET sii_environment = company.dte_sii_environment "
            "FROM res_company AS company "
            "WHERE company.id = caf_auth.company_id "
            "AND caf_auth.sii_environment IS NULL"
        )
    cr.execute(
        "UPDATE l10n_cl_caf_authorization "
        "SET sii_environment = 'certification' "
        "WHERE sii_environment IS NULL"
    )
    cr.execute("SELECT to_regclass('l10n_cl_caf_trust_key')")
    if cr.fetchone()[0]:
        cr.execute(
            "ALTER TABLE l10n_cl_caf_trust_key "
            "ADD COLUMN IF NOT EXISTS scope varchar"
        )
        cr.execute(
            "UPDATE l10n_cl_caf_trust_key SET scope = 'official' WHERE scope IS NULL"
        )
        cr.execute(
            "ALTER TABLE l10n_cl_caf_trust_key "
            "DROP CONSTRAINT IF EXISTS "
            "l10n_cl_caf_trust_key_caf_trust_key_id_start_uniq"
        )
