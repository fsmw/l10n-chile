# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Shared fixtures for the RCV test suite."""

from odoo.tests import TransactionCase


class RcvTestCommon(TransactionCase):
    """Base class for RCV suites that need a Chilean company.

    The optional DTE localization used to provide the Chilean chart to the test
    company. These suites build it themselves so the addon can be tested on a
    plain accounting database.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.company
        chile = cls.env.ref("base.cl")
        if company.country_id != chile:
            company.country_id = chile.id
        if not company.chart_template:
            cls.env["account.chart.template"].try_loading(
                "cl", company=company, install_demo=False
            )
        ensure_journals(cls.env)


def ensure_journals(env):
    """Make sure the company has the journals an invoice needs.

    The suite runs on a plain accounting database, without the Chilean chart
    that the optional DTE localization used to install.
    """
    Journal = env["account.journal"]
    company = env.company
    for journal_type, code in (("purchase", "RCVP"), ("sale", "RCVS")):
        if not Journal.search(
            [("type", "=", journal_type), ("company_id", "=", company.id)],
            limit=1,
        ):
            Journal.create(
                {
                    "name": f"RCV {journal_type}",
                    "code": code,
                    "type": journal_type,
                    "company_id": company.id,
                }
            )
