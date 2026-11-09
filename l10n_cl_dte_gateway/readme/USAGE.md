Requires OCA queue_job with the server jobrunner configured. Do not
automatically resend an attempt with an uncertain outcome.

Demo routes to the local `DemoSiiGateway`, which creates deterministic
synthetic receipts, `DEMO-*` TrackIDs, status responses, and reconciliation
results without an HTTP/SOAP client. All response artifacts are hashed and
marked `DEMO / SIMULADO`. A simulated acceptance is not an SII receipt.

Certification envelopes use the SII seed/token SOAP services, certificate-signed
authentication, multipart DTEUpload, and QueryEstUp on `maullin.sii.cl`. Configure
one valid Certification signing profile, its private key, and the issuer and
authorized sender RUTs. Tokens remain in memory and are never stored in attempts.
Production transport uses a separate signing profile and production CAF records;
never reuse Certification material as Production evidence.

From a certification execution, use **Test SII Authentication** to check the
certificate without sending a DTE. Successful authentication records a 30-minute
proof bound to the certificate, private-key identity, issuer and sender; no token
is persisted. **Validate Full SII Set** checks the document evidence, transport
configuration and this authentication proof. **Submit Complete Set to SII**
queues the immutable envelope; the execution changes to submitted only after an
actual SII receipt returns a TrackID. Open **SII Submission** to query the result
and inspect the protected response evidence. Receipt, processing, acceptance,
observations, partial rejection and rejection are distinct outcomes. EPR alone
does not prove acceptance: every document-type counter must cover the envelope.

The jobrunner must be loaded with `server_wide_modules = web,queue_job` and a
nonzero channel capacity, for example `[queue_job] channels = root:1`. Envelope
functions use the `root.sii_envelope` channel. The deployment database must be
explicitly selected. Tests mock HTTP and never contact SII.

Dispatch claims are committed in a separate cursor transaction before external
I/O. This small independent transaction is required to prevent a rolled-back
job from sending the same envelope again. A connection loss after upload stays
unknown and cannot be resent. An explicit retry is available only when the
failure is confirmed to precede upload. Query retries never upload a document.
If a worker is lost without obtaining a TrackID, retain the sending/unknown
record and reconcile the SII receipt; do not reset it to pending.

Protocol references: [SII automation documentation](https://www.sii.cl/factura_electronica/factura_mercado/instructivo.htm),
and the Certification WSDLs `CrSeed.jws?WSDL`, `GetTokenFromSeed.jws?WSDL` and
`QueryEstUp.jws?WSDL`. The current WSDL bindings use `http://DefaultNamespace`;
the historical PDF examples show endpoint namespaces instead.

Installation alone does not prove live interoperability with SII. Certificate
authentication must pass before a set becomes ready. A successful web/CAF
portal login does not establish a usable DTE web-service token and is never
accepted as authentication proof by this adapter.


SII reception format and correction
----------------------------------

Certification uploads require the ISO-8859-1 declaration on line 1 and the
complete EnvioDTE opening element with schemaLocation on line 2. The sender
RUT must identify the certificate holder, not the company owning its record.
Invalid wire headers and sender identities are rejected before authentication.


Refreshing asynchronous envelope responses
-----------------------------------------

**Refresh Response** reloads the retained transmission evidence in the same
dialog. It never contacts SII or repeats a transmission. **Query SII Status**
queues a status query only when a TrackID is available in a queryable state;
it keeps the response dialog open. Refresh again after background processing.


Certification uploads are also blocked before authentication if the cover uses
a resolution number other than 0. An RCT status response permits an audited
cover correction only after verification of its fingerprint, TrackID and the
original upload receipt. No transmission history is rewritten.

An EPR response means that SII processed the envelope; its document counters
determine acceptance or rejection. The submission and Certification execution
show the rejected-document summary, and the submission displays the counters
by document type. These values are checked against the retained response, its
TrackID and the original envelope. QueryEstUp does not provide individual
rejection reasons; obtain those from the validation result sent by SII to the
company's registered contact email. An EPR document rejection does not unlock
the cover-only correction or authorize an automatic resend.
# Production preparation

Use **DTE → Configuration → Production SII Readiness** to review the production
setup for the DTE types the company plans to issue (33, 52, 56 and 61). The
checklist inspects the SII resolution and sender identity, production signing
profile, and a valid CAF with an available folio for each selected type.

This review is local only. It does not contact or authenticate with the SII,
change the company's environment, reserve folios, or issue a DTE. A successful
local result is not an SII approval; perform the separate SII authentication
and operational checks before production dispatch.
