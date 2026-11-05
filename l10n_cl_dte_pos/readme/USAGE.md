## Configuration

Install this addon with the Chilean DTE account and boleta addons. In
the company's Chilean DTE settings, select the POS accounting mode. The
setting is company-wide and applies to sessions opened after the change.

## Modes

- Invoice per POS order: use the regular Odoo POS invoice and issue the
  DTE from its `account.move`.
- DTE per order, accounting at session close: issue the DTE from the
  persisted paid `pos.order` and use the native POS session closing move
  for accounting.

Boleta preparation is currently available only in Demo mode. The direct
POS source validates the paid order, line detail, issuer, receiver, CLP
totals, CAF, signed evidence, and the company-wide session policy before
preparing a boleta.

For boletas exceeding the annual 135 UF threshold, configure the SII
payment method on every POS payment method. If an order uses multiple
methods, they must all map to the same SII payment code; issuance is
blocked otherwise.

The session closing check requires one queued DTE per Chilean POS order
and compares each immutable snapshot total with its POS order total. It
then uses Odoo's native session closing move. SII acceptance and Demo
simulation remain distinct states; this addon does not enable official
transport.

Refunds and exchanges containing negative quantities require a referenced
credit note. This POS flow currently rejects them before posting an invoice or
issuing another boleta, in both accounting modes. Mixed sales/returns are also
blocked. Use the supported accounting credit-note flow for invoice sources;
direct POS credit-note issuance remains a later delivery.

Closing summary sessions checks every order's prepared DTE and signed-source
total through both the cash-control action and direct session-close action.
The session counters include invoice-sourced DTEs in standard mode.

Automated browser tests exercise company policy controls, cash and split
payments, refund rejection, counted cash and native session closing. Their
fixtures use synthetic Demo CAFs and certificates; they do not contact the SII.
