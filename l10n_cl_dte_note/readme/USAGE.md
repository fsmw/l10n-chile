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
In Certification and Production mode the referenced DTE must be accepted
in the company's current mode; the note then follows that mode's SII
submission queue, as described in the SII gateway addon.

A private root-first chain-lock prefix is available for the national transport
workflow. It remains dormant until coordinated transport and issuance activation.

Discounted detail lines include both the percentage and its CLP amount.
The discount amount uses decimal half-up rounding; the snapshot line
subtotal is preserved.

Notes also reference invoices with SII additional taxes (for example ILA)
and global discounts. Each note detail keeps the additional tax code
(`CodImpAdic`) of the original line it adjusts, with the same rate, and the
note totals carry one `ImptoReten` per code with the note's own amounts.
A debit note increasing amounts applies the same codes and rates and
cannot carry a global discount.

A full cancellation reproduces the original global discounts
(`DscRcgGlobal`) so its totals equal the original totals. For partial
credits the global discounts are allocated per line: each original line
receives a share of the taxable or exempt discounts proportional to its
amount (integer CLP, largest remainder, line order on ties), and a credit
of part of a line takes the same proportion of the line's remaining share,
rounded half up; the last credit of a line takes the whole remaining share.
Keep the credit note's discount lines at the amount this rule gives; the
issuance error states it when they differ. The available amounts are net
of discounts and include additional taxes per line, per tax code and in
total, so the sum of notes cannot exceed the original. Text corrections
remain without amounts.
