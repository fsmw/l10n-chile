from unittest.mock import patch

from odoo.addons.l10n_cl_dte_core.tests.common import DteTestCase


class GatewayTestCase(DteTestCase):
    def create_submission(self, scenario="accepted", source_key=None):
        document = self.create_document(source_key=source_key)
        self.put_document_in_queued_state(document)
        submission = (
            self.env["l10n_cl.electronic.submission"]
            .with_user(self.dte_manager)
            .create(
                {
                    "document_id": document.id,
                    "environment": "demo",
                    "demo_scenario": scenario,
                    "idempotency_key": f"submission-{document.id}-{scenario}",
                }
            )
        )
        return document, submission

    def send_without_external_xml_validation(self, submission):
        model_class = type(submission)
        with patch.object(model_class, "_validate_document_payload", return_value=True):
            submission.with_user(self.dte_manager)._job_send_to_sii()

    def query_status(self, submission):
        model_class = type(submission)
        with patch.object(model_class, "_validate_document_payload", return_value=True):
            submission.with_user(self.dte_manager)._job_query_sii_status()
