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
