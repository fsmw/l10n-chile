Requires OCA queue_job with the server jobrunner configured. Do not
automatically resend an attempt with an uncertain outcome.

Demo routes to the local `DemoSiiGateway`, which creates deterministic
synthetic receipts, `DEMO-*` TrackIDs, status responses, and reconciliation
results without an HTTP/SOAP client. All response artifacts are hashed and
marked `DEMO / SIMULADO`. A simulated acceptance is not an SII receipt.

Certification uses a fixed-host adapter for multi-DTE upload and QueryEstUp.
It authenticates for each operation, retains the raw SII response, stores a
successful upload TrackID, and classifies ambiguous upload failures without
blindly resending. Production remains blocked and never falls back to Demo.

Call ``l10n_cl.sii.gateway.factory.check_envelope_readiness(company_id,
sender_vat, environment)`` to validate that the configured ``RutEnvia``
matches the active Certification certificate holder and that the adapter
accepts the company configuration. It returns readiness and diagnostics
without sending a request. Queueing and background sending repeat these checks.

The Certification authentication client obtains a seed and signs the token
request with the company's active, non-synthetic Certification certificate.
It connects only to the fixed Certification host, verifies TLS, rejects HTTP
redirects and bounds response sizes. Tokens remain in memory; callers must close
the client to clear its session. Tests use mocked HTTP and synthetic responses;
they never contact SII.
