DTE users read the register from DTE Issuance > Purchase and Sales Register.
*Periods* opens the periods of the current year, creating the missing ones;
*Open Fiscal Year* does the same for another year.

To read a period from the SII, a DTE Manager clicks *Read from SII* on the
period, or opens *Read from SII* and selects the company, operation (purchases
or sales), year and month. The company needs a valid RUT and an active
production signing profile (DTE Issuance > Configuration) whose certificate has
its private key and is authorized at the SII for the company. The read is
queued and processed in the background by the *SII RCV: Process Queued Reads*
scheduled action; only one read per period is queued at a time. The result is
stored as a new read of the period, with its coverage by SII state. A failed
read records the failed step (sign-in, session token, RCV portal, session
configuration, session load, session validation, session parameters or RCV
start data) and its message; a period that already holds a stored read keeps
it. Purchase details that the SII is still preparing (pending documents) are
reported as unavailable and leave the read partial.

For a daily read, a user who can edit companies enables *Daily SII RCV Read*
on the company (Chilean DTE tab) and selects the *RCV Read User*, an active DTE
Manager of that company, recorded as the requester. The *SII RCV: Schedule
Daily Reads* scheduled action then queues, once per Chilean day, the purchases
and sales of the current and previous month.

The certificate and its private key are written only to a private temporary
directory (files readable by the Odoo user only) for the duration of the read
and removed afterwards; they are never logged. Requests use bounded timeouts,
and a rate-limited or failing query is retried once.

To import a CSV, a DTE Manager opens *Import CSV*, selects the company,
operation (purchases or sales), year and month, and uploads the file. The CSV
can use commas or semicolons and UTF-8 or Latin-1 encoding. These columns are
required: `tipo_documento`, `rut_contraparte`, `folio`, `fecha`, `neto`,
`exento`, `iva` and `total`; `razon_social`, `iva_no_recuperable` and
`iva_uso_comun` are optional. The headers of the detail files downloaded from
the SII RCV portal (`Tipo Doc`, `RUT Proveedor` or `Rut cliente`, `Folio`,
`Fecha Docto`, `Monto Neto`, `Monto Exento`, `Monto IVA Recuperable` or
`Monto IVA`, `Monto Total`...) are accepted as well.

The whole file is rejected when it is empty, larger than 10 MB or lacks a
required column. A row with an invalid document type, folio, RUT, date or
amount, or a document repeated in the file, is not imported: it is listed on
the resulting read, which is marked as partial (failed when no row is valid).
The imported file and its SHA-256 are kept on the read.

Each read reconciles its documents when it is stored: the scheduled SII read
with superuser rights, a CSV import with the rights of the DTE Manager who
imports it. Without access to journal entries the documents stay pending. The
documents list can be filtered by reconciliation state (missing in
accounting, differences, indeterminate, matched). On a document, the
*Reconciliation* group shows the linked journal entry, the reason code, the
message and the VAT comparison; the *Candidate Entries* tab lists the entries
with its document type, folio and RUT, and the *Reconciliation Log* tab lists
every decision.

A DTE Manager with access to invoices can:

- reconcile again a document (*Reconcile*), the selected documents (action
  *Reconcile* of the list) or a whole period (*Reconcile* on the period);
- link a document to one of its candidate entries (*Link Entry*), for example
  when several entries share its identity, explaining the review behind it.
  The link is compared at once and later reconciliations keep it while the
  entry remains a candidate;
- unlink a document (*Unlink Entry*), with a reason. Later reconciliations do
  not link it again until it is linked manually.

On vendor bills and customer invoices, the *RCV State* tells whether the last
read of the period reports the document (reported, not reported, RCV
incomplete), and the *RCV* button opens the RCV documents linked to it. The
filters *Not Reported in the RCV*, *RCV Incomplete* and *Reported in the RCV*
are available on the invoice lists. The *RCV Book* report is printed from the
period.

A purchase that declares zero VAT in the RCV but leaves an unexplained residual
in its total stays in manual review: the RCV alone does not explain it.

Records are restricted to the companies the user works in. DTE users can read
the register and its reconciliation; DTE Managers can also read it from the
SII, import it, reconcile it and link or unlink documents.
