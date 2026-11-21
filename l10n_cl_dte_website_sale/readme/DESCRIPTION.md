Adds a **Tax Document** step to the eCommerce checkout of websites whose
company is Chilean. The customer chooses a boleta (final consumer, no RUT
needed) or a factura, which requires a valid Chilean RUT, the business name,
the business activity (giro) and a billing address in Chile with street and
commune. The choice is validated on the server and stored on the sales order.

When the order is invoiced, manually or by the automatic invoicing of the
shop, the invoice gets the matching DTE type: 39 (or 41 when no line bears
VAT) for a boleta, 33 (or 34) for a factura. Boletas go to the customer when
its partner holds a Chilean RUT and to the generic final consumer otherwise;
facturas go to the partner holding the RUT, which is created from the checkout
data when it does not exist. The existing **Issue DTE** button and boleta flow
then issue the right document. Other websites and companies are unaffected.
