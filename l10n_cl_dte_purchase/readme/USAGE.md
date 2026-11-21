1. Load a CAF for DTE type 46.
2. Give the supplier a valid Chilean RUT, a name, an address and a commune.
3. Create a vendor bill with document type 46. On each taxable line set the
   19% purchase VAT and the total VAT withholding of the Chilean chart
   (`19% WH`, SII code 15). Exempt lines carry no tax.
4. Confirm the bill and press **Issue DTE**. The company issues and signs the
   DTE 46 and sends it to the SII as it does for customer invoices.

Bills with a partial VAT retention, a second category withholding, another
currency or negative lines are refused before a folio is reserved.
