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

This release does not reconcile RCV documents with journal entries; that
feature is planned as a separate addon.
