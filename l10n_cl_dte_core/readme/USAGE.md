Install this technical addon as a dependency of a DTE feature addon.
Only DTE managers can create documents and evidence or advance a
document state. Lifecycle changes pass through the validated transition
action and create an immutable audit event in the same database
transaction.

Evidence hashes and byte counts are calculated from the stored bytes.
Binary evidence is restricted to DTE managers because the common
evidence store can contain CAF source material. DTE users can inspect
document and evidence metadata within their allowed companies.
Documents, artifacts and events cannot be deleted; evidence and audit
events cannot be edited.

Preparation states follow
`draft → authorized → numbered → prepared → signed → queued`. Fiscal
states are maintained separately and represent SII submission and
response states. TrackID or transport receipt must not be interpreted as
fiscal acceptance.
