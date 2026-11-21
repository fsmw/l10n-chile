1. Load a CAF for DTE type 43.
2. Give the principal a valid Chilean RUT, a name, an economic activity
   (*giro*), an address and a commune.
3. Create a vendor bill for the principal with document type 43:
   * one positive line per operation made on the principal's behalf, with the
     19% purchase VAT or no VAT and its *Settled Document Type*;
   * one negative line per commission or other charge, with *Agent Charge* set
     and the 19% sales VAT or no VAT. Its label is the `Glosa` of the charge.
4. Confirm the bill and press **Issue DTE**. The company numbers, signs and
   sends the DTE 43 to the SII as it does for customer invoices.

Bills without a charge line, with a positive charge, a negative or discounted
operation, a settled operation without its document type, another currency,
additional taxes or withholdings, or a sales VAT on an operation (or a
purchase VAT on a charge) are refused before a folio is reserved.

Credit and debit notes on a 43, its printed representation and receiving a 43
from another agent are not supported yet.
