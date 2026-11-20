Requests CAF folio authorizations from the SII with an auditable, idempotent
workflow, and keeps folio stock replenished through per-company rules.

- The SII issues CAFs through its certificate-authenticated web assistant
  (`cvc_cgi/dte/of_solicita_folios`). This addon owns the client of that
  assistant: AUT2000 login with the company certificate over in-memory mTLS
  (PycURL), the request form, the folio allocation and the CAF download, and
  the re-download of already authorized ranges (`rf_reobtencion*`). The host
  follows the explicit environment of each request: Certification on
  maullin, Production on palena, with the same paths. `l10n_cl_dte_certification`
  reuses the same client fixed to Certification.
- Requests move through `pending`, `approved`, `running`, `success`,
  `partial`, `failed`, `uncertain`, `reconciled` and `cancelled`. Each run is a
  numbered attempt, committed as running before any SII call. Right before
  the POST that makes the SII allocate folios, the attempt is durably marked
  as dispatched together with the folio range the assistant reserved.
- The exact SII download response (the CAF) is stored as an attachment with
  its SHA-256 digest. The CAF is registered and validated through
  `l10n_cl_dte_caf` before any folio can be reserved from it.
- Error codes distinguish login/authentication, certificate, SII rejection,
  unused-folio blocking, quantity limit, CAF validation and post-dispatch
  uncertainty. Stored messages are fixed texts; certificates, keys and
  session cookies are never logged or stored.
- A failure after the allocation POST makes the request `uncertain`; it is
  never sent again. **Recover CAF from SII** downloads the CAF of the
  recorded range (it never allocates folios) and reconciles the request; an
  operator may also reconcile it with a CAF obtained independently. Transient
  failures before allocation can be retried up to three attempts.
- Folio availability comes from the validated CAFs and the reservation ledger
  of `l10n_cl_dte_caf`; this addon keeps no ledger of its own.
- Replenishment rules per company, DTE type and environment queue a request
  when availability falls to the threshold. Queued requests wait for operator
  approval unless the rule explicitly opts in to automatic execution, which
  only the scheduled job performs. Acquisition never runs inside invoice
  creation, numbering or signing.

Port of the CAF acquisition of the SMWLAB module `l10n_cl_edi_caf_auto`, on
top of the SII assistant client first written for `l10n_cl_dte_certification`.
