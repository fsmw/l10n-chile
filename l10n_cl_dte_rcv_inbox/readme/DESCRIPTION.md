This module connects the SII purchase and sales register (RCV) of
`l10n_cl_dte_rcv` with the inbox of received DTEs of `l10n_cl_dte_inbox` and its
vendor bills of `l10n_cl_dte_inbox_account`.

- An RCV purchase and the received DTE with the same company, issuer RUT,
  document type and folio are linked when either of them arrives: when an RCV
  read or CSV import stores the purchase, and when the inbox registers the DTE
  (by import or by email). Both forms show the link.
- The RCV reconciliation also considers the vendor bills bound to the received
  DTE, and the signed XML of that DTE, already validated by the inbox, proves
  the VAT of a purchase that the RCV reports with zero VAT. Such a document is
  matched with the reason *Source document confirms the VAT*.
- A DTE manager can prepare the vendor bill draft of a purchase missing in
  accounting. When the inbox holds the received DTE, the draft is prepared from
  its signed XML by the incoming DTE vendor bill preview, with its lines, taxes,
  global discounts and additional taxes. Otherwise it is prepared from the RCV
  amounts, one line per VAT component, for review.
- Optionally, a daily scheduled action prepares those drafts automatically for
  the companies that enable it.

Drafts are dated in the RCV tax period and are never posted.
