Open an issued DTE and go to the **Receiver Responses** tab. A DTE manager
can press **Check Receiver Response** to query the SII registry at once. The
scheduled action *DTE: Check Receiver Responses in the SII Registry* polls
the documents of the legal window every hour, with a backoff from one hour
up to one day, makes a last check when the eight-day window closes and then
stops following the document. It also stops once a content claim, a total
lack of goods claim, or both an acceptance and a goods receipt are recorded.

Emailed `RespuestaDTE` and `EnvioRecibos` files are read by the DTE intake
mailbox configured in `l10n_cl_dte_inbox`. Review them under
**Accounting > DTE Issuance > Operations > Receiver Responses**; use the
*Not Matched* filter to find files that refer to no DTE of the company.
The invoice form shows the receiver state as a badge.
