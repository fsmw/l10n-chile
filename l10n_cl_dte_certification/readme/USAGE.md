Import an original SII certification set from the DTE Operations menu, under
Certification Sets and Import Original SET. The source is stored as evidence.
DTE managers can analyze numbered sections, ordered cases, document types, and
source lines, then create immutable case snapshots that preserve the exact
instructions, source digest, and line ranges. Unrecognized document types stay
visible as unsupported cases. This does not create accounting documents or
consume folios. Re-importing the same bytes for the same company is rejected.
No source data is included in this addon.

DTE managers can interpret case snapshots after reviewing them. This
versioned pass extracts only explicit detail columns, note references and
correction markers. It keeps source-line provenance for extracted values and
reports unrecognized or incomplete instructions for review. It does not create
accounting documents, resolve business partners or products, reserve folios, or
send information to the SII.

The case coverage summary separates interpreted cases from those needing
review, unsupported document types, and cases not yet interpreted. The coverage
check reports these counts and stays in warning state until every case has been
reviewed successfully.

Recognized detail rows are materialized as ordered, immutable case-line
snapshots. Each row retains its explicit values, source line, source digest,
and interpreter version for later operator mapping.

Item quantities, prices, and discounts are checked with decimal arithmetic.
Out-of-range values remain visible with their source evidence and put the case
in review; they are never silently corrected.

Global discounts, note references and reasons, note operations, text corrections,
and interpretation diagnostics are also shown as structured, read-only case
facts with their original line references.

Recognized note reasons map to a separate SII reference code (`CodRef`); the
source reason remains intact for `RazonRef`. Unknown reasons and note/document
type mismatches require review. When a newer interpreter is introduced, a DTE
manager must explicitly reinterpret outdated cases; the previous and new
interpretations are retained in an immutable history.
