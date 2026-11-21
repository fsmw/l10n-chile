# Configuration

The Chilean chart of accounts has no sale-side VAT retention tax, so the bridge
requires an explicit configuration and refuses to prepare a DTE 46 until it is
complete:

1. Create a dedicated sale journal that uses documents for received purchase
   invoices. The folios belong to each buyer's CAF and must not collide with the
   numbering of our own invoices.
2. Choose the sales VAT 19% tax (SII code 14), price excluded.
3. Create a sale tax "VAT retained by the buyer": percent, -19%, SII code 15,
   price excluded, whose invoice tax repartition line posts to a balance account
   such as "VAT retained by third parties" (an asset that offsets the declared
   output VAT).
4. In Accounting settings, under **Incoming Chilean DTE**, select the journal and
   both taxes. The **Incoming DTE Vendor Bill Configuration** role is required.

Operators need the **Incoming DTE Vendor Bills** role, and the buyer must exist as
a partner with its valid Chilean RUT and taxpayer type.
