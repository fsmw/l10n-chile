# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from psycopg2 import IntegrityError

from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import DispatchTestCommon


@tagged("post_install", "-at_install")
class TestDispatchElectronicDocument(DispatchTestCommon):
    def _create_document(self, dispatch):
        return self.env["l10n_cl.electronic.document"].create(
            {
                "name": f"DTE 52 {dispatch.name}",
                "company_id": dispatch.company_id.id,
                "sii_environment": dispatch.company_id.dte_sii_environment,
                "document_code": "52",
                "source_key": f"dispatch:{dispatch.source_key}",
                "source_dispatch_id": dispatch.id,
            }
        )

    def test_registry_maps_dispatch_to_its_document_field(self):
        # Arrange
        registry = self.env["l10n_cl.dte.source.registry"]
        # Act / Assert
        self.assertEqual(
            registry._dte_source_relation("l10n_cl.dte.dispatch"),
            "source_dispatch_id",
        )

    def test_document_links_back_to_its_dispatch(self):
        # Arrange
        dispatch = self._create_dispatch()
        # Act
        document = self._create_document(dispatch)
        # Assert
        self.assertEqual(dispatch.document_ids, document)

    def test_dispatch_with_documents_cannot_be_deleted(self):
        # Arrange
        dispatch = self._create_dispatch()
        self._create_document(dispatch)
        # Act / Assert
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                dispatch.unlink()
