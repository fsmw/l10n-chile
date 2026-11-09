# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError

from ..services.official_set_preflight import OfficialCertificationSetPreflight
from .common import DteCertificationTestCase


class TestTransportReadiness(DteCertificationTestCase):
    def _check_readiness(self, transport):
        run = self.env["l10n_cl.dte.certification.run"].new({})
        writes = []
        with (
            patch.object(type(run), "_check_execution_access", return_value=True),
            patch.object(
                type(run),
                "_write_internal",
                lambda record, values: writes.append(values),
            ),
            patch.object(
                OfficialCertificationSetPreflight,
                "check_run",
                return_value={"digest": "digest", "diagnostics": [], "warnings": []},
            ),
            patch.object(
                type(self.env["l10n_cl.sii.gateway.factory"]),
                "get_envelope_gateway",
                return_value=transport,
            ),
        ):
            result = run._run_official_preflight()
        return result, writes[0]

    def test_unavailable_transport_blocks_otherwise_valid_set(self):
        transport = MagicMock()
        transport.validate_configuration.side_effect = UserError("Certificate missing")
        result, values = self._check_readiness(transport)
        self.assertEqual(values["reportability_state"], "blocked")
        self.assertEqual(
            result["diagnostics"][0]["code"], "certification_transport_unavailable"
        )

    def test_available_transport_is_required_for_ready_state(self):
        transport = MagicMock()
        result, values = self._check_readiness(transport)
        transport.validate_configuration.assert_called_once()
        transport.validate_authentication_state.assert_called_once()
        self.assertEqual(values["reportability_state"], "ready")
        self.assertFalse(result["diagnostics"])

    def test_expired_authentication_blocks_otherwise_available_transport(self):
        transport = MagicMock()
        transport.validate_authentication_state.side_effect = UserError("Expired")
        result, values = self._check_readiness(transport)
        self.assertEqual(values["reportability_state"], "blocked")
        self.assertEqual(
            result["diagnostics"][0]["code"], "certification_transport_unavailable"
        )
