# Received purchase invoices (DTE 46)

When a buyer issues an electronic purchase invoice (DTE 46) for goods or services
it bought from us, the signed XML arrives in our inbox with the buyer as issuer
and our company as receiver. It documents our own sale. This bridge books it as a
native customer invoice draft built from the signed XML:

- revenue lines from the XML detail (net amounts);
- sales VAT 19% (SII code 14), declared as our output VAT;
- the VAT the buyer retained (``ImptoReten``) as a sale-side "VAT retained by
  the buyer" tax: the total retention (code 15) or, per detail, the partial
  product retention code (30-48) the buyer declared, each booked with the tax
  configured for that code, so the receivable equals ``MntTotal``.

The draft keeps the buyer's folio and document type 46, is linked to the
immutable inbox evidence through the shared binding, and is never offered for
DTE issuance from our side. The inbox acceptance and claim actions stay
available.
