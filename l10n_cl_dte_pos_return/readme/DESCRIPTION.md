Issue referenced NC61 credit notes for full or partial returns of POS boletas 39/41.
Returns can take place at another store of the same company in CLP. The original
sale determines the accounting mode:

- Standard: a native posted accounting credit note is the fiscal source.
- Session summary: the negative POS order is the fiscal source; native session
  closing accounts for the return without an individual accounting credit.

The addon reuses native payments, inventory returns, session closing and the DTE
issuance, evidence, note-reference and A4 report services. Root locking and native
refund quantities prevent adjustments beyond the original fiscal and physical
capacity. The currently supported fiscal environment is Demo.
