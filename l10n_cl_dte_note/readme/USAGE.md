This first implementation supports DTE issuance in Demo mode for Chilean
CLP companies. The source DTE must have been issued and accepted locally
by the same company and receiver. Supported credit references are types
33, 34, 39, 41 and 61; debit notes support invoices 33/34 and
cancellation of an accepted type 61.

Create the credit or debit note using the standard Odoo accounting
wizard. The note retains one referenced DTE, its fiscal reason,
operation and original line ordinals. Publish the accounting move, then
use **Issue DTE** to reserve its own CAF folio, build and sign the
DTE_v10 XML, create a durable Demo submission and retain the request and
response evidence. Demo mode does not contact the SII.

Type 61 supports full cancellation, partial return and amount reduction.
Type 56 supports an amount increase or one full cancellation of an
accepted type 61. Partial adjustments are limited to 60 details and
cannot exceed remaining amounts or returned quantities. Unsupported
references and operations are rejected before folio reservation.

The module extends `account.move`, `account.move.line`,
`account.move.reversal` and `account.debit.note`; it does not replace
the accounting entries or create separate fiscal folio sequences.
Production SII transport and certification-mode note issuance are
outside this release.
