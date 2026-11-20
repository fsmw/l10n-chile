To prepare a vendor bill draft:

1. Open an RCV purchase in *Missing in Accounting* state and click *Prepare
   Draft Bill*.
2. Without a received DTE, review the supplier, the journal and the lines
   proposed from the RCV amounts, with their accounts and taxes. When no
   supplier has the RUT of the document, tick *Create the Supplier* and enter
   its name; a supplier is never created otherwise.
3. With a received DTE, click *Prepare from the Received DTE*: the incoming DTE
   vendor bill preview opens, dated in the RCV period. The received DTE must be
   reviewed and locally validated in the inbox.
4. The draft is linked to the RCV document by the reconciliation.

To prepare the drafts automatically, enable *Automatic RCV Vendor Bill Drafts*
in the SII settings of the company and select the *RCV Draft User*: an active
DTE manager with the *Incoming DTE Vendor Bills* role and access to the
company. Every day, the scheduled action *SII RCV: Prepare Missing Vendor Bill
Drafts* prepares the draft of each registered purchase missing in accounting,
with the rights of that user. It never creates suppliers and never posts; the
reason a draft could not be prepared is shown on the RCV document.

Use *Find Received DTE* on an RCV purchase, or *Find Received DTEs* on a
selection, to link purchases stored before the module was installed.
