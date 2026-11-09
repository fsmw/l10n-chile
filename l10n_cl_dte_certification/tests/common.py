# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import uuid

from odoo.tests.common import new_test_user

from odoo.addons.l10n_cl_dte_core.tests.common import DteTestCase


class DteCertificationTestCase(DteTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.certification_manager = new_test_user(
            cls.env,
            login="dte-certification-manager",
            groups=(
                "base.group_user,base.group_system,base.group_partner_manager,"
                "account.group_account_invoice,l10n_cl_dte_core.group_dte_manager"
            ),
            company_id=cls.company.id,
            company_ids=[(6, 0, [cls.company.id])],
        )
        cls.company.partner_id.with_user(cls.certification_manager).write(
            {
                "country_id": cls.env.ref("base.cl").id,
                "vat": "12345678-5",
            }
        )
        cls.company.with_user(cls.certification_manager).write(
            {
                "country_id": cls.env.ref("base.cl").id,
                "dte_sii_environment": "certification",
            }
        )

    def create_certification_process(self, record_postulation=True, **values):
        values.setdefault("company_id", self.company.id)
        if values.get("process_mode", "new_postulation") == "new_postulation":
            values.setdefault("sii_application_reference", uuid.uuid4().hex)
        process = (
            self.env["l10n_cl.dte.certification.process"]
            .with_user(self.certification_manager)
            .with_company(self.company)
            .create(values)
        )
        if record_postulation and process.process_mode == "new_postulation":
            stage = process.stage_ids.filtered(lambda item: item.code == "assigned_set")
            requirement = stage.requirement_ids.filtered(
                lambda item: item.code == "accepted_postulation"
            )
            attachment = (
                self.env["ir.attachment"]
                .with_user(self.certification_manager)
                .sudo()
                .create(
                    {
                        "name": "synthetic-postulation-acceptance.txt",
                        "type": "binary",
                        "raw": b"Synthetic accepted SII postulation for tests",
                        "mimetype": "text/plain",
                        "res_model": process._name,
                        "res_id": process.id,
                        "public": False,
                    }
                )
            )
            evidence = (
                self.env["l10n_cl.dte.certification.evidence"]
                .with_user(self.certification_manager)
                ._create_from_source(
                    {
                        "stage_id": stage.id,
                        "requirement_id": requirement.id,
                        "name": attachment.name,
                        "evidence_type": "postulation_acceptance",
                        "origin": "sii_portal",
                        "attachment_id": attachment.id,
                        "external_reference": process.sii_application_reference,
                    }
                )
            )
            evidence.action_mark_source_verified()
            requirement._record_external_result(evidence, accepted=True)
        return process
