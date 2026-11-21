Assigns ("cesión electrónica", Ley 19.983) an issued Chilean electronic
invoice (DTE 33 or 34) accepted by the SII to a factoring company.

The addon builds the Archivo Electrónico de Cesión (AEC) from the exact signed
DTE bytes kept as evidence by `l10n_cl_dte_core`, adds the assignment data
(assignor and authorized signer, assignee, amount, last due date and the sworn
declaration), signs the DTECedido, Cesion and AEC elements with the company
certificate, and validates the file against the official SII schema
`AEC_v10.xsd` bundled in `static/xsd`.

The signed AEC is uploaded to the SII Registro de Transferencia de Créditos
(RTC) with the certificate-signed seed/token authentication of
`l10n_cl_dte_gateway`, in Certification (maullin) or Production (palena). Every
upload and status query keeps its request and response digests and raw
evidence. An upload whose answer is lost becomes *Outcome Unknown* and is
never sent again automatically.

Accounting is not changed: the addon records the assignment, the factoring
company and the SII TrackID on the invoice.
