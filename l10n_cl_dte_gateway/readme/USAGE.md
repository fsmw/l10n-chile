Requires OCA queue_job with the server jobrunner configured. Do not
automatically resend an attempt with an uncertain outcome.

Demo routes to the local `DemoSiiGateway`, which creates deterministic
synthetic receipts, `DEMO-*` TrackIDs, status responses, and reconciliation
results without an HTTP/SOAP client. All response artifacts are hashed and
marked `DEMO / SIMULADO`. A simulated acceptance is not an SII receipt.

Certification and Production currently have no transport adapters. The
factory rejects those modes before queueing work or creating an attempt; it
never falls back to Demo. Add and configure the real adapter before enabling
either mode for outbound SII traffic.
