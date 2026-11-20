# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import os
from unittest.mock import patch

import requests

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_signing.tests.common import SigningTestCase

from ..services import sii_rcv_client as client
from ..services.sii_rcv_client import USER_AGENT, SiiRcvClient, SiiRcvError
from .common import RcvTestCase
from .sii_fakes import (
    NO_JSON,
    SYNTHETIC_TOKEN,
    FakeResponse,
    FakeSiiSession,
    detail_row,
    ok,
    summary_row,
)

SESSION_PATH = "odoo.addons.l10n_cl_dte_rcv.services.sii_rcv_client.requests.Session"


class SiiReadCase(SigningTestCase, RcvTestCase):
    """Run SII reads against a recorded synthetic portal; never the network."""

    def setUp(self):
        super().setUp()
        sleep_patcher = patch.object(client.time, "sleep")
        self.sleep = sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    def _production_profile(self, private=True):
        return self.create_signing_profile(environment="production", private=private)

    def _read(self, sync, session):
        with patch(SESSION_PATH, return_value=session) as factory:
            state = sync.sudo()._run_sii_read()
        return state, factory

    def _queued_read(self, operation="purchase", month=8):
        period = self._period(operation=operation, month=month)
        return period, period._queue_sii_read(self.manager)


@tagged("post_install", "-at_install")
class TestRcvSiiRead(SiiReadCase):
    def setUp(self):
        super().setUp()
        self._production_profile()

    def _complete_session(self):
        return FakeSiiSession(
            summaries={"REGISTRO": [summary_row(2)]},
            details={("REGISTRO", "33"): [detail_row(501), detail_row(502)]},
        )

    def test_read_stores_documents_and_sets_states(self):
        period, sync = self._queued_read()
        self.assertEqual(period.state, "pending")
        session = self._complete_session()
        state, _factory = self._read(sync, session)
        self.assertEqual(state, "success")
        self.assertEqual(sync.state, "success")
        self.assertTrue(sync.finished_at)
        self.assertEqual(sync.detail_count, 2)
        self.assertEqual(sync.summary_ids.document_count, 2)
        self.assertEqual(len(sync.section_ids), 3)
        self.assertEqual(set(sync.section_ids.mapped("state")), {"complete"})
        self.assertEqual(period.state, "success")
        self.assertEqual(period.current_sync_id, sync)
        self.assertTrue(period.last_sync_at)
        documents = self.env["l10n_cl.dte.rcv.document"].search(
            period._document_domain()
        )
        self.assertEqual(sorted(documents.mapped("folio")), ["501", "502"])
        self.assertEqual(set(documents.mapped("issuer_vat")), {"76222222-1"})
        summaries = session.facade_calls("getResumen")
        self.assertEqual(
            [call["estadoContab"] for call in summaries],
            ["REGISTRO", "RECLAMADO", "PENDIENTE"],
        )
        self.assertEqual(summaries[0]["rutEmisor"], "76111111")
        self.assertEqual(summaries[0]["dvEmisor"], "6")
        self.assertEqual(summaries[0]["ptributario"], "202608")
        self.assertEqual(summaries[0]["operacion"], "COMPRA")

    def test_session_is_safe_and_key_material_is_removed(self):
        _period, sync = self._queued_read()
        session = self._complete_session()
        self._read(sync, session)
        self.assertEqual(session.key_file_modes, [0o600, 0o600])
        self.assertFalse(any(os.path.exists(path) for path in session.cert))
        self.assertFalse(os.path.exists(os.path.dirname(session.cert[0])))
        self.assertFalse(session.trust_env)
        self.assertTrue(session.verify)
        self.assertEqual(session.headers["User-Agent"], USER_AGENT)
        self.assertTrue(all(kwargs.get("timeout") for *_x, kwargs in session.calls))
        wrapped = [
            kwargs["json"]["metaData"]
            for _verb, url, kwargs in session.calls
            if url.startswith(client.FACADE_URL)
        ]
        self.assertTrue(wrapped)
        self.assertEqual(
            {meta["conversationId"] for meta in wrapped}, {SYNTHETIC_TOKEN}
        )

    def test_key_material_is_removed_when_the_read_fails(self):
        _period, sync = self._queued_read()
        session = FakeSiiSession(
            overrides={client.PORTAL_URL: requests.ConnectionError("synthetic")}
        )
        self._read(sync, session)
        self.assertEqual(sync.state, "failed")
        self.assertFalse(os.path.exists(os.path.dirname(session.cert[0])))

    def test_sales_read_only_asks_the_register(self):
        _period, sync = self._queued_read(operation="sale")
        session = FakeSiiSession(
            summaries={"REGISTRO": [summary_row(1)]},
            details={
                ("REGISTRO", "33"): [detail_row(77, rut=12345678, check_digit="5")]
            },
        )
        self.assertEqual(self._read(sync, session)[0], "success")
        self.assertEqual(
            [call["operacion"] for call in session.facade_calls("getResumen")],
            ["VENTA"],
        )
        self.assertEqual(
            session.facade_calls("getDetalleVenta")[0]["accionRecaptcha"], "RCV_DETV"
        )
        document = sync.document_ids
        self.assertEqual(document.issuer_vat, "76111111-6")
        self.assertEqual(document.counterpart_vat, "12345678-5")

    def test_pending_detail_not_ready_marks_partial(self):
        period, sync = self._queued_read()
        session = FakeSiiSession(
            summaries={"PENDIENTE": [summary_row(1)]},
            details={("PENDIENTE", "33"): ok([], code=99)},
        )
        self.assertEqual(self._read(sync, session)[0], "partial")
        pending = sync.section_ids.filtered(lambda s: s.sii_state == "PENDIENTE")
        self.assertEqual(pending.state, "partial")
        self.assertEqual(period.state, "partial")

    def test_rejected_summary_marks_section_failed(self):
        _period, sync = self._queued_read()
        session = FakeSiiSession(
            overrides={
                client.FACADE_URL + "getResumen": lambda kwargs: ok([], code=17)
                if kwargs["json"]["data"]["estadoContab"] == "RECLAMADO"
                else ok([])
            }
        )
        self.assertEqual(self._read(sync, session)[0], "partial")
        failed = sync.section_ids.filtered(lambda s: s.state == "failed")
        self.assertEqual(failed.sii_state, "RECLAMADO")

    def test_failed_read_keeps_the_last_stored_read(self):
        period, sync = self._queued_read()
        self._read(sync, self._complete_session())
        retry = period._queue_sii_read(self.manager)
        session = FakeSiiSession(overrides={client.AUTH_URL: FakeResponse(503)})
        self.assertEqual(self._read(retry, session)[0], "failed")
        self.assertEqual(period.state, "success")
        self.assertEqual(period.current_sync_id, sync)

    def test_each_bootstrap_failure_fails_the_read_clearly(self):
        cases = {
            "auth": {client.AUTH_URL: FakeResponse(500)},
            "token": {client.AUTH_URL: FakeResponse(200)},
            "portal": {client.PORTAL_URL: FakeResponse(503)},
            "configuration": {client.CONFIGURATION_URL: FakeResponse(500)},
            "session": {client.SESSION_URL: requests.Timeout("synthetic")},
            "token_check": {client.TOKEN_CHECK_URL: FakeResponse(401)},
            "parameters": {client.PARAMETERS_URL: FakeResponse(payload=NO_JSON)},
            "start": {client.FACADE_URL + "getDatosInicio": ok({}, code=3)},
        }
        period = self._period()
        for step, overrides in cases.items():
            with self.subTest(step=step):
                sync = period._queue_sii_read(self.manager)
                session = FakeSiiSession(overrides=overrides)
                self.assertEqual(self._read(sync, session)[0], "failed")
                self.assertEqual(sync.error_code, step)
                self.assertTrue(sync.error_message)
                self.assertTrue(sync.finished_at)
                self.assertFalse(sync.section_ids)
                self.assertFalse(session.facade_calls("getResumen"))
                self.assertEqual(period.state, "failed")

    def test_rejected_parameters_name_the_step(self):
        _period, sync = self._queued_read()
        session = FakeSiiSession(overrides={client.PARAMETERS_URL: ok({}, code=1)})
        self._read(sync, session)
        self.assertEqual(sync.error_code, "parameters")
        self.assertIn("session parameters", sync.error_message)

    def test_cron_processes_queued_reads(self):
        _period, sync = self._queued_read()
        with patch(SESSION_PATH, return_value=self._complete_session()):
            self.env["l10n_cl.dte.rcv.sync"].sudo()._cron_process_pending()
        self.assertEqual(sync.state, "success")


@tagged("post_install", "-at_install")
class TestRcvSiiCertificate(SiiReadCase):
    def _assert_refused_before_request(self, sync):
        session = FakeSiiSession()
        state, factory = self._read(sync, session)
        self.assertEqual(state, "failed")
        self.assertEqual(sync.state, "failed")
        self.assertTrue(sync.error_message)
        factory.assert_not_called()
        self.assertFalse(session.calls)

    def test_missing_certificate_is_refused_before_any_request(self):
        _period, sync = self._queued_read()
        self._assert_refused_before_request(sync)
        self.assertIn("signing profile", sync.error_message)

    def test_demo_profile_is_not_used(self):
        self.create_signing_profile(environment="demo")
        _period, sync = self._queued_read()
        self._assert_refused_before_request(sync)

    def test_certificate_without_private_key_is_refused(self):
        self._production_profile(private=False)
        _period, sync = self._queued_read()
        self._assert_refused_before_request(sync)
        self.assertIn("private key", sync.error_message)

    def test_invalid_company_rut_is_refused(self):
        self._production_profile()
        self.company.vat = False
        _period, sync = self._queued_read()
        self._assert_refused_before_request(sync)
        self.assertEqual(sync.error_code, "company_rut")

    def test_client_raises_for_missing_certificate(self):
        _period, sync = self._queued_read()
        with patch(SESSION_PATH) as factory, self.assertRaises(UserError):
            SiiRcvClient(self.env).fetch(sync)
        factory.assert_not_called()

    def test_non_manager_cannot_use_the_certificate(self):
        self._production_profile()
        _period, sync = self._queued_read()
        sync = sync.with_user(self.reader)
        with patch(SESSION_PATH) as factory, self.assertRaises(AccessError):
            SiiRcvClient(sync.env).fetch(sync)
        factory.assert_not_called()


@tagged("post_install", "-at_install")
class TestRcvSiiStart(SiiReadCase):
    def test_receipt_sales_are_refused(self):
        period = self._period(operation="boleta")
        with self.assertRaisesRegex(UserError, "boletas"):
            period.with_user(self.manager).action_sync()
        self.assertFalse(period.sync_ids)
        sync = self._sync(period)
        with patch(SESSION_PATH) as factory:
            with self.assertRaises(SiiRcvError) as caught:
                SiiRcvClient(self.env).fetch(sync)
        self.assertEqual(caught.exception.code, "receipt_unsupported")
        factory.assert_not_called()

    def test_only_managers_start_a_read(self):
        period = self._period()
        with self.assertRaises(AccessError):
            period.with_user(self.reader).action_sync()
        with self.assertRaises(AccessError):
            self.env["l10n_cl.dte.rcv.sync.wizard"].with_user(self.reader).create(
                {"year": 2026, "month": 8, "operation": "purchase"}
            )
        self.assertFalse(period.sync_ids)

    def test_manager_queues_one_read_per_period(self):
        period = self._period()
        result = period.with_user(self.manager).action_sync()
        self.assertEqual(result["tag"], "display_notification")
        period.with_user(self.manager).action_sync()
        self.assertEqual(len(period.sync_ids), 1)
        sync = period.sync_ids
        self.assertEqual(sync.state, "queued")
        self.assertEqual(sync.source, "sii")
        self.assertEqual(sync.requested_by, self.manager)
        self.assertEqual(period.state, "pending")

    def test_wizard_queues_the_selected_period(self):
        wizard = (
            self.env["l10n_cl.dte.rcv.sync.wizard"]
            .with_user(self.manager)
            .create({"year": 2026, "month": 9, "operation": "sale"})
        )
        action = wizard.action_sync()
        period = self.env["l10n_cl.dte.rcv.period"].browse(action["res_id"])
        self.assertEqual((period.month, period.operation), (9, "sale"))
        self.assertEqual(period.sync_ids.state, "queued")


@tagged("post_install", "-at_install")
class TestRcvSiiClientTransport(SiiReadCase):
    """Cases ported from the source module's transport tests."""

    def setUp(self):
        super().setUp()
        self.client = SiiRcvClient(self.env)

    def test_functional_error_is_not_accepted_as_empty_success(self):
        session = FakeSiiSession(
            overrides={client.FACADE_URL + "getResumen": ok([], code=17)}
        )
        with self.assertRaises(SiiRcvError):
            self.client._call(session, "getResumen", {}, "opaque", require_code=True)

    def test_missing_code_is_rejected_when_required(self):
        session = FakeSiiSession(
            overrides={client.FACADE_URL + "getResumen": FakeResponse(payload={})}
        )
        with self.assertRaises(SiiRcvError):
            self.client._call(session, "getResumen", {}, "opaque", require_code=True)

    def test_non_object_json_is_rejected(self):
        session = FakeSiiSession(
            overrides={client.FACADE_URL + "getResumen": FakeResponse(payload=[])}
        )
        with self.assertRaises(SiiRcvError):
            self.client._call(session, "getResumen", {}, "opaque", require_code=True)

    def test_rate_limit_retries_once_and_caps_retry_after(self):
        answers = [FakeResponse(429, headers={"Retry-After": "999"}), ok([])]
        session = FakeSiiSession(overrides={client.FACADE_URL + "getResumen": answers})
        result = self.client._call(
            session, "getResumen", {}, "opaque", require_code=True
        )
        self.assertEqual(result["data"], [])
        self.assertEqual(len(session.calls), 2)
        self.sleep.assert_called_once_with(30)

    def test_persistent_rate_limit_is_reported(self):
        session = FakeSiiSession(
            overrides={client.FACADE_URL + "getResumen": FakeResponse(429)}
        )
        with self.assertRaises(SiiRcvError) as caught:
            self.client._call(session, "getResumen", {}, "opaque")
        self.assertEqual(caught.exception.code, "rate_limit")
        self.assertEqual(len(session.calls), 2)

    def test_server_error_and_connection_error_retry_once(self):
        for failure in (FakeResponse(502), requests.ConnectionError("synthetic")):
            with self.subTest(failure=type(failure).__name__):
                session = FakeSiiSession(
                    overrides={client.FACADE_URL + "getResumen": [failure, ok([])]}
                )
                self.client._call(session, "getResumen", {}, "opaque")
                self.assertEqual(len(session.calls), 2)

    def test_retry_after_is_bounded(self):
        for value in ("Wed, 21 Oct 2037 07:28:00 GMT", "0", "-5", "soon", None):
            with self.subTest(value=value):
                delay = self.client._retry_after(value)
                self.assertGreaterEqual(delay, 1)
                self.assertLessEqual(delay, 30)
        self.assertEqual(self.client._retry_after("7"), 7)


@tagged("post_install", "-at_install")
class TestRcvSiiSchedule(SiiReadCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.other_company = cls.env["res.company"].create(
            {"name": "RCV Schedule Company B", "vat": "76543210-3"}
        )
        cls.company.write(
            {"dte_rcv_daily_read": True, "dte_rcv_read_user_id": cls.manager.id}
        )

    def _scheduled(self, company):
        return self.env["l10n_cl.dte.rcv.sync"].search(
            [("company_id", "=", company.id), ("scheduled_for", "!=", False)]
        )

    def test_cron_reads_opted_in_companies_once_a_day(self):
        sync_model = self.env["l10n_cl.dte.rcv.sync"].sudo()
        self.assertEqual(sync_model._cron_schedule_daily_reads(), 4)
        self.assertEqual(sync_model._cron_schedule_daily_reads(), 0)
        scheduled = self._scheduled(self.company)
        self.assertEqual(len(scheduled), 4)
        self.assertEqual(set(scheduled.mapped("operation")), {"purchase", "sale"})
        self.assertEqual(len(set(scheduled.mapped("period_id"))), 4)
        self.assertEqual(scheduled.requested_by, self.manager)
        self.assertEqual(set(scheduled.mapped("state")), {"queued"})
        self.assertFalse(self._scheduled(self.other_company))
        self.assertFalse(
            self.env["l10n_cl.dte.rcv.sync"].search(
                [("company_id", "=", self.other_company.id)]
            )
        )

    def test_cron_does_not_duplicate_a_queued_read(self):
        today = fields.Date.context_today(
            self.env["l10n_cl.dte.rcv.sync"].with_context(tz="America/Santiago")
        )
        period = self._period(year=today.year, month=today.month)
        manual = period._queue_sii_read(self.manager)
        sync_model = self.env["l10n_cl.dte.rcv.sync"].sudo()
        self.assertEqual(sync_model._cron_schedule_daily_reads(), 3)
        self.assertEqual(period.sync_ids, manual)
        self.assertEqual(period._queue_sii_read(self.manager), manual)

    def test_cron_skips_companies_with_an_invalid_read_user(self):
        self.manager.sudo().active = False
        with self.assertLogs("odoo.addons.l10n_cl_dte_rcv.models.rcv_sync", "WARNING"):
            queued = (
                self.env["l10n_cl.dte.rcv.sync"].sudo()._cron_schedule_daily_reads()
            )
        self.assertEqual(queued, 0)

    def test_daily_read_requires_a_manager(self):
        with self.assertRaises(UserError):
            self.other_company.write(
                {"dte_rcv_daily_read": True, "dte_rcv_read_user_id": self.reader.id}
            )
        with self.assertRaises(UserError):
            self.other_company.write({"dte_rcv_daily_read": True})
