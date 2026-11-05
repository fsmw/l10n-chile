# Usage

Use **Accounting → DTE Issuance → Operations → Incoming DTEs → Import Incoming
DTE** to upload a single DTE, EnvioDTE or EnvioBOLETA. The result opens an immutable
import record with the original file, SHA-256, company, environment, operator,
counts and one result per DTE. **Import History** includes invalid uploads.

The import checks the local XSD, each DTE XML signature, TED/FRMT, header/TED
identity and totals, the envelope signature, addressee and declared document
counts. Boleta schemas are used when the `l10n_cl_dte_boleta` addon resources are
available; an unavailable schema is explicitly unsupported. No schemas or keys
are downloaded. Boleta envelopes use the existing local
`EnvioBOLETA_v11_compat.xsd` adaptation. The schema filename used is retained in
the validation evidence; this does not assert official acceptance. XML with
DTD/entities, foreign wrappers, duplicate IDs, more than
2000 DTEs or files exceeding 20 MiB is blocked.

Imports are atomic: if the file or any DTE is invalid, no incoming documents are
registered from that upload. Inspect file diagnostics and open the individual
result lines for the failing stage, message and XSD location. Fix the source and
upload a new file; existing evidence cannot be overwritten. Reimporting identical
bytes returns the existing import. Valid new envelopes reuse existing identical
DTEs; a matching fiscal identity with different signed content is a conflict.

Each accepted incoming record links back to its source imports. Original file
bytes remain in the import, while each extracted DTE retains the inherited XML
namespaces needed to check its signature. Legacy single-document integrations
retain `_import_xml()` and the incoming-document return type: invalid schema or
signatures remain visible as invalid records. **Validate XML** checks historical
incoming evidence again without changing its XML. The receiving environment is
snapshotted and does not follow subsequent company setting changes.

Schema compliance and cryptographic integrity do not establish certificate trust,
revocation status, signer authorization or SII acceptance. The signer certificate
fingerprint is retained when present, with trust explicitly unverified. **Mark
Reviewed** records a human review; it is not acceptance. No purchase invoice is
created automatically, no response is transmitted and no certification stage is
completed by importing or reviewing. Response transport remains disabled.
