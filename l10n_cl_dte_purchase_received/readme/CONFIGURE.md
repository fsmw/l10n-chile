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
4. For each partial VAT retention code your buyers use, create a sale tax:
   percent, the negative official rate, the SII code, price excluded, with the
   same kind of tax account. Supported codes and rates: 30 (-10%), 31 (-19%),
   32 (-8%), 33 (-8%), 34 (-4%), 36 (-10%), 37 (-10%), 38 (-19%), 39 (-19%),
   41 (-19%), 47 (-19%) and 48 (-14%), from the SII "Formato Documentos
   Tributarios Electrónicos" v2.5 (2026-02). The settings refuse a tax whose
   code is not supported, whose rate is not the official one or whose code is
   already mapped.
5. In Accounting settings, under **Incoming Chilean DTE**, select the journal,
   both taxes and the partial retention taxes. The **Incoming DTE Vendor Bill
   Configuration** role is required.

Operators need the **Incoming DTE Vendor Bills** role, and the buyer must exist as
a partner with its valid Chilean RUT and taxpayer type.
