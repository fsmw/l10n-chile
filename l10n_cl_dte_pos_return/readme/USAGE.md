1. Open the receiving POS session and select Return.
2. Find an accepted original POS boleta 39/41 from any store of this company.
3. Select quantities and enter a reference reason of 1 to 90 Latin-1 characters.
4. Review the online preview and choose a manual refund payment method.
5. Validate online. Hand over the refund only after server confirmation. The
   cashbox opens after the confirmed NC61, and the operational receipt identifies
   the credit note and its original boleta.

If the response is uncertain, reconnect and validate the same order and UUID.
Do not create another return or pay the customer again. A confirmed server
validation error leaves an editable draft; an uncertain response preserves the
order identity until it can be recovered.

A complete untouched original uses cancellation reference code 1. Partial returns
and remaining quantities after earlier adjustments use reference code 3. The
server recomputes amounts with Odoo's tax engine and validates both the financial
balance and previously returned quantities. Native POS prices stay unchanged;
NC61 serializes the original net fiscal unit price.

For older sales without line identity evidence, a DTE manager uses Prepare Legacy
Line Mapping on the original order. Only an exact unique correspondence is
accepted; ambiguous duplicate details block the return without guessing or
rewriting the original snapshot or XML.

Lotted products require one original lot per POS detail. Serial numbers must come
from the original native stock movement and cannot be returned twice. The return
uses the receiving store's picking type. Fiscal order lines, payments and lot
identities become immutable after issuance.

A DTE manager can create and generate the existing A4 print sample from the NC61
in either accounting mode. Its source is the signed XML, including PDF417 and the
original reference. The POS operational receipt does not replace this report.
Queued means pending fiscal confirmation. Rejected or uncertain credit notes
preserve payment, stock and evidence and block session closing for review.
