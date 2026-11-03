import base64
import hashlib
import uuid
from unittest.mock import patch

from odoo.tests.common import TransactionCase, new_test_user


class DteTestCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Domain services translate messages without owning an Odoo model
        # environment. CI invokes them outside an HTTP request, so use the
        # active test environment's language as Odoo's translation context.
        cls._translation_patcher = patch(
            "odoo.tools.translate._get_lang", return_value=cls.env.lang
        )
        cls._translation_patcher.start()
        cls.company = cls.env.company
        cls.dte_manager = new_test_user(
            cls.env,
            login="dte-test-manager",
            groups=(
                "base.group_user,base.group_partner_manager,"
                "account.group_account_invoice,l10n_cl_dte_core.group_dte_manager"
            ),
            company_id=cls.company.id,
            company_ids=[(6, 0, [cls.company.id])],
        )
        cls.dte_user = new_test_user(
            cls.env,
            login="dte-test-user",
            groups="base.group_user,l10n_cl_dte_core.group_dte_user",
            company_id=cls.company.id,
            company_ids=[(6, 0, [cls.company.id])],
        )

    @classmethod
    def tearDownClass(cls):
        cls._translation_patcher.stop()
        super().tearDownClass()

    def create_document(self, **values):
        vals = {
            "name": "DTE test document",
            "company_id": self.company.id,
            "sii_environment": self.company.dte_sii_environment,
            "document_code": "33",
            "source_key": f"test-source-{uuid.uuid4().hex}",
        }
        vals.update(values)
        if vals.get("source_key") is None:
            vals["source_key"] = f"test-source-{uuid.uuid4().hex}"
        return (
            self.env["l10n_cl.electronic.document"]
            .with_user(self.dte_manager)
            .with_company(self.company)
            .create(vals)
        )

    def create_artifact(self, document, role, payload=b"synthetic evidence"):
        return (
            self.env["l10n_cl.electronic.artifact"]
            .with_user(self.dte_manager)
            .with_company(document.company_id)
            .create(
                {
                    "document_id": document.id,
                    "role": role,
                    "content": base64.b64encode(payload),
                    "mimetype": "application/octet-stream",
                    "producer_version": "tests/18.0",
                }
            )
        )

    def put_document_in_queued_state(self, document):
        if document.preparation_state == "draft":
            caf = self.create_artifact(document, "caf_source")
            document.action_record_transition(
                "authorized", "test_caf_validated", artifact_id=caf.id
            )
            document.action_record_transition(
                "numbered", "test_folio_reserved", artifact_id=caf.id, folio_number=1
            )
        snapshot = self.create_artifact(document, "source_snapshot", b"{}")
        document.action_record_source_snapshot(snapshot.id)
        unsigned = self.create_artifact(document, "unsigned_xml", b"<DTE/>")
        document.action_record_transition(
            "prepared", "test_xml_prepared", artifact_id=unsigned.id
        )
        signed = self.create_artifact(
            document, "signed_xml", b"<DTE><Signature/></DTE>"
        )
        document.action_record_transition(
            "signed", "test_xml_signed", artifact_id=signed.id
        )
        request = self.create_artifact(document, "sii_request", b"<DTE/>")
        document.action_record_transition(
            "queued", "test_submission_queued", artifact_id=request.id
        )
        return signed, request


def digest(payload):
    return hashlib.sha256(payload).hexdigest()
