DTE users read the register from DTE Issuance > Purchase and Sales Register.
*Periods* opens the periods of the current year, creating the missing ones;
*Open Fiscal Year* does the same for another year.

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

Records are restricted to the companies the user works in. DTE users can read
the register; DTE Managers can also import.
