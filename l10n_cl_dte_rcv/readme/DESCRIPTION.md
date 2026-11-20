Storage for the SII purchase and sales register (Registro de Compras y Ventas,
RCV), read-only download of the register from the SII, and import of its
detail CSV.

The RCV is kept by period (company, year, month and operation: purchases,
sales or receipt sales). Each read of a period, from the SII or from a CSV
file, is stored as a read with its coverage by SII state, the
summary totals by document type, and the fiscal documents it reports. A
document is identified by company, operation, document type, issuer RUT and
folio; every read updates it and keeps an observation with the reported
amounts, so the history of a document across reads is preserved.

A section whose detail does not prove its summary (missing rows, different
counts or totals) is kept as partial. The absence of a document is never
inferred from an incomplete read.

The SII read signs in to the SII website with the company's production
signing certificate (mutual TLS) and calls, read-only, the services of the RCV
pages: the summary of each RCV state (registered, claimed and pending for
purchases; registered for sales) and the detail of each document type. Reads
run in the background, and companies can opt in to a daily read of the current
and previous month. Receipt sales (boletas) cannot be read from the SII yet,
because there is no validated SII service for them.

Each stored document is reconciled with the journal entries of its company.
Purchases are matched with vendor bills and sales with customer invoices by
document type, folio and the RUT of the commercial partner; for sales, our
issued DTE with that type and folio is preferred (a DTE rejected by the SII is
ignored). The net, exempt, VAT and total amounts and the accounting period are
then compared, and the document is marked as matched, missing in accounting,
duplicate, with a period or amount difference, for manual review or
indeterminate, with a reason code and a message. For purchases, the
recoverable, non-recoverable and common use VAT allocation is compared
separately: it only qualifies a fiscal match.

A document whose RCV section was read partially or failed is never reported
as missing in accounting: it stays indeterminate. Likewise, a journal entry of
the period is reported as not in the RCV only when every SII state of the
operation (registered, claimed and pending for purchases; registered for
sales) was read completely; otherwise it is marked as RCV incomplete. A CSV
import never proves that coverage.

The reconciliation never writes journal entries. Every automatic decision is
recorded once in the reconciliation log with its evidence; manual links and
unlinks are recorded with their reason. The period report (RCV Book) prints the
summary and the documents of the current read with their reconciliation state.
