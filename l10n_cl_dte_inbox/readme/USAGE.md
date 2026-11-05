# Usage

Import one DTE XML from **Accounting → DTE Issuance → Operations → Incoming DTEs**.
The import checks that the XML is well-formed, contains exactly one DTE, has
the required identity fields, and addresses the current company. The original
bytes and SHA-256 digest are retained. Exact duplicate imports return the
existing record.

The imported XML is not treated as proof of SII origin, schema validity, or
cryptographic signature validity. Certification extends the incoming record
with an explicit link to the Information Exchange stage. Stage readiness stays
blocked until trusted signature validation and an official response transport
are implemented and evidenced. Those network actions are currently disabled.
