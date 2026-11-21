Requirements:

- the invoice is a posted, unpaid customer invoice with DTE type 33 or 34,
  issued in Certification or Production and accepted by the SII;
- the company has its RUT, street, city and email, and one active signing
  profile for the SII mode; the certificate holder must be authorized by the
  company in the SII to assign documents;
- the factoring company has a Chilean RUT, street, city and email.

Steps, as a DTE manager:

1. On the invoice press **Assign to Factoring**, select the factoring company
   and review the amount, the last due date and the sworn declaration
   (pre-filled with the SII wording; required when the merchandise receipt is
   not electronic). Save.
2. Press **Sign AEC**. The AEC is signed, validated against `AEC_v10.xsd` and
   stored as evidence.
3. Press **Send to SII**. The upload runs as a queue job and stores the SII
   receipt and its TrackID.
4. Press **Check SII Status** until the assignment is *Assigned* (`EOK`) or
   *Rejected*.

If the upload answer is lost, the assignment becomes *Outcome Unknown*. It is
never uploaded again automatically. Press **Check SII Registry** to ask the SII
whether the DTE is recorded as assigned, or check the RTC portal, and cancel
the assignment only when you are sure SII did not register it.

DTE users can read assignments, evidence and attempts; only DTE managers act.

Accounting: the addon does not create journal entries. When the factoring
company pays, record the transfer of the receivable yourself, for example with
a journal entry that credits the customer receivable (reconciled with the
invoice) and debits the factoring company receivable or the bank, plus the
factoring cost.
