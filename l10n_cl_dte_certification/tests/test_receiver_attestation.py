# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from types import SimpleNamespace
from unittest.mock import MagicMock

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_certification.wizards.receiver_confirmation_wizard import (
    DteCertificationReceiverConfirmationWizard,
)

from .common import DteCertificationTestCase


@tagged("post_install", "-at_install")
class TestDteCertificationReceiverAttestation(DteCertificationTestCase):
    def _wizard(self, classification, reference=None):
        partner = SimpleNamespace(id=24, vat="12345678-5")
        certification_set = SimpleNamespace(
            id=7,
            state="ready",
            can_edit_envelope=True,
            company_id=SimpleNamespace(partner_id=SimpleNamespace(id=99)),
            _check_certification_access=MagicMock(),
        )
        case = SimpleNamespace(
            partner_id=partner,
            set_id=certification_set,
            case_identifier="SET-CASO-1",
            _write_internal=MagicMock(),
        )
        env = MagicMock()
        env.user.id = 42
        env.user.has_group.return_value = True
        env["l10n_cl.caf.authorization"]._normalize_vat.return_value = "12345678-5"
        env["l10n_cl.caf.authorization"]._is_valid_vat.return_value = True
        env["l10n_cl.dte.certification.process"].search.return_value = False
        wizard = SimpleNamespace(
            ensure_one=MagicMock(),
            case_id=case,
            classification=classification,
            verification_reference=reference,
            env=env,
        )
        return wizard, case

    def test_existing_customer_attestation_requires_reference(self):
        wizard, case = self._wizard("existing_customer")
        with self.assertRaises(ValidationError):
            DteCertificationReceiverConfirmationWizard.action_apply(wizard)
        case._write_internal.assert_not_called()

    def test_synthetic_receiver_remains_explicitly_classified(self):
        wizard, case = self._wizard("synthetic_uat")
        DteCertificationReceiverConfirmationWizard.action_apply(wizard)
        values = case._write_internal.call_args.args[0]
        self.assertEqual(values["receiver_classification"], "synthetic_uat")
        self.assertFalse(values["receiver_verification_reference"])
        self.assertEqual(values["receiver_verified_by"], 42)
