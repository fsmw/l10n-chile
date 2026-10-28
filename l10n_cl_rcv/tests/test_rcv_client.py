# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import time
from unittest.mock import Mock, patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase


class TestRcvClientTransport(TransactionCase):
    def _response(self, status=200, payload=None, headers=None):
        response = Mock()
        response.status_code = status
        response.headers = headers or {}
        response.json.return_value = payload
        return response

    def test_functional_error_is_not_accepted_as_empty_success(self):
        client = self.env["l10n.cl.rcv.sii.client"]
        session = Mock()
        session.post.return_value = self._response(
            payload={"respEstado": {"codRespuesta": 17}, "data": []}
        )
        with self.assertRaises(UserError):
            client._call(session, "getResumen", {}, "opaque", {}, require_code=True)

    def test_non_object_json_is_rejected(self):
        client = self.env["l10n.cl.rcv.sii.client"]
        session = Mock()
        session.post.return_value = self._response(payload=[])
        with self.assertRaises(UserError):
            client._call(session, "getResumen", {}, "opaque", {}, require_code=True)

    def test_rate_limit_retries_once_and_caps_retry_after(self):
        client = self.env["l10n.cl.rcv.sii.client"]
        session = Mock()
        session.post.side_effect = [
            self._response(status=429, headers={"Retry-After": "999"}),
            self._response(payload={"respEstado": {"codRespuesta": 0}, "data": []}),
        ]
        with patch.object(time, "sleep") as sleep:
            result = client._call(
                session, "getResumen", {}, "opaque", {}, require_code=True
            )
        self.assertEqual(result["data"], [])
        self.assertEqual(session.post.call_count, 2)
        sleep.assert_called_once_with(30)

    def test_retry_after_http_date_is_bounded(self):
        client = self.env["l10n.cl.rcv.sii.client"]
        self.assertGreaterEqual(client._retry_after("Wed, 21 Oct 2037 07:28:00 GMT"), 1)
        self.assertLessEqual(client._retry_after("Wed, 21 Oct 2037 07:28:00 GMT"), 30)
