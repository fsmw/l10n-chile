DTE Managers with billing access configure the shared catalog from DTE Issuance >
Configuration > Export Customs Codes. Enter the code, category, description, source URL
and source date after checking the current public customs tables.

DTE users and billing users have read access. Managers can create, edit and
archive entries, but cannot delete them. Archived codes cannot be used for new
export snapshots. Configuration is shared across companies; a syntactically
valid code is not proof of current official validity.

The existing Accounting navigation requires billing access; the catalog ACLs
also allow DTE-only roles through direct actions or integrations.

Integrations can store service, recipient identification, nationality, destination
and an explicit CLP exchange rate with date/source on accounting drafts. Editing
requires accounting access and a DTE user (DTE Manager for types 111/112), an
allowed company, and no linked DTE evidence, including archived evidence. Copies
preserve recipient/service data but clear the exchange rate and its provenance.
These initial fields do not enable issuance or validate fiscal completeness;
customs inputs, source/line freezing and the export invoice UI follow separately.
