Storage for the SII purchase and sales register (Registro de Compras y Ventas,
RCV) and import of its detail CSV.

The RCV is kept by period (company, year, month and operation: purchases,
sales or receipt sales). Each read of a period, from a CSV file or from a
later SII integration, is stored as a read with its coverage by SII state, the
summary totals by document type, and the fiscal documents it reports. A
document is identified by company, operation, document type, issuer RUT and
folio; every read updates it and keeps an observation with the reported
amounts, so the history of a document across reads is preserved.

A section whose detail does not prove its summary (missing rows, different
counts or totals) is kept as partial. The absence of a document is never
inferred from an incomplete read.

This release does not query the SII and does not reconcile RCV documents with
journal entries; those features are planned as separate addons.
