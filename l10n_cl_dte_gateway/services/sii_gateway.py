# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hashlib
import xml.etree.ElementTree as ElementTree

from odoo import _, api, models
from odoo.exceptions import UserError, ValidationError

DEMO_NAMESPACE = "urn:oca:l10n-chile:dte:demo:v1"
ElementTree.register_namespace("demo", DEMO_NAMESPACE)


class SiiGateway:
    """Outbound port for an SII transport implementation."""

    def send_document(
        self, company, environment, request_bytes, idempotency_key, scenario
    ):
        raise NotImplementedError

    def query_status(
        self, company, environment, track_id, scenario, query_number, idempotency_key
    ):
        raise NotImplementedError

    def reconcile_submission(self, company, environment, identity, scenario):
        raise NotImplementedError


class DemoSiiGateway(SiiGateway):
    """Deterministic local SII simulator; this class deliberately has no I/O."""

    _scenarios = {
        "accepted",
        "rejected",
        "processing_then_accepted",
        "unknown_then_reconciled",
    }

    def send_document(
        self, company, environment, request_bytes, idempotency_key, scenario
    ):
        self._check_environment(environment)
        self._check_scenario(scenario)
        track_id = self._track_id(idempotency_key)
        state = (
            "outcome_unknown"
            if scenario == "unknown_then_reconciled"
            else "submitted"
        )
        return {
            "state": state,
            "track_id": False if state == "outcome_unknown" else track_id,
            "raw_response": self._response(
                "send", state, idempotency_key, track_id, scenario
            ),
            "is_simulated": True,
        }

    def query_status(
        self, company, environment, track_id, scenario, query_number, idempotency_key
    ):
        self._check_environment(environment)
        self._check_scenario(scenario)
        if track_id != self._track_id(idempotency_key):
            raise ValidationError(
                _("The Demo TrackID does not match its submission identity.")
            )
        if scenario == "rejected":
            state = "rejected"
        elif scenario == "processing_then_accepted" and query_number <= 1:
            state = "processing"
        else:
            state = "accepted"
        return {
            "state": state,
            "track_id": track_id,
            "raw_response": self._response(
                "query", state, track_id, track_id, scenario
            ),
            "is_simulated": True,
        }

    def reconcile_submission(self, company, environment, identity, scenario):
        self._check_environment(environment)
        self._check_scenario(scenario)
        if scenario != "unknown_then_reconciled":
            raise ValidationError(
                _("Only the unknown-then-reconciled Demo scenario can be reconciled.")
            )
        idempotency_key = identity["idempotency_key"]
        track_id = self._track_id(idempotency_key)
        return {
            "state": "submitted",
            "track_id": track_id,
            "raw_response": self._response(
                "reconcile", "submitted", idempotency_key, track_id, scenario
            ),
            "is_simulated": True,
        }

    @staticmethod
    def _check_environment(environment):
        if environment != "demo":
            raise UserError(_("The Demo gateway only accepts the Demo environment."))

    @classmethod
    def _check_scenario(cls, scenario):
        if scenario not in cls._scenarios:
            raise ValidationError(_("Select a valid Demo response scenario."))

    @staticmethod
    def _track_id(identity):
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20].upper()
        return f"DEMO-{digest}"

    @staticmethod
    def _response(operation, state, identity, track_id, scenario):
        root = ElementTree.Element(
            f"{{{DEMO_NAMESPACE}}}DemoSiiResponse",
            {
                "environment": "demo",
                "simulated": "true",
                "operation": operation,
                "state": state,
                "scenario": scenario,
            },
        )
        ElementTree.SubElement(root, f"{{{DEMO_NAMESPACE}}}Identity").text = identity
        if track_id:
            ElementTree.SubElement(root, f"{{{DEMO_NAMESPACE}}}TrackID").text = track_id
        return ElementTree.tostring(root, encoding="UTF-8", xml_declaration=True)


class SiiGatewayFactory(models.AbstractModel):
    _name = "l10n_cl.sii.gateway.factory"
    _description = "DTE SII Gateway Factory"

    @api.model
    def get_gateway(self, environment):
        if environment == "demo":
            return DemoSiiGateway()
        if environment in {"certification", "production"}:
            raise UserError(
                _(
                    "No SII transport adapter is installed for %(environment)s. "
                    "The DTE has not been sent."
                )
                % {"environment": environment}
            )
        raise ValidationError(_("The DTE SII environment is invalid."))
