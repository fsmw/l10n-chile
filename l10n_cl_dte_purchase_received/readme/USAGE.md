# Usage

1. Import the DTE 46 into the inbox and mark it as reviewed after the local
   schema, signature and TED validation succeeds.
2. Click **Prepare Customer Invoice**. Review the buyer, amounts and revenue
   accounts per detail, then **Refresh Preview** and **Confirm Draft**.
3. Post the customer invoice natively. Posting re-checks the invoice against the
   signed XML. The **Issue DTE** action is not available for it.

The resulting entry for a 1,000 net sale with total retention is: receivable
1,000 debit, VAT retained by the buyer 190 debit, revenue 1,000 credit and sales
VAT 190 credit.

With a partial retention, for example cattle (code 32 at 8%) on a 1,000 net
sale, the receivable is ``MntTotal`` 1,110: receivable 1,110 debit, VAT retained
by the buyer (code 32) 80 debit, revenue 1,000 credit and sales VAT 190 credit.
A document whose partial code has no configured tax is refused with
``retention_unmapped``; a rate other than the official one, an ``IVANoRet``
that is not VAT minus the retentions, or a total retention that differs from
the VAT is refused before a draft is created.
