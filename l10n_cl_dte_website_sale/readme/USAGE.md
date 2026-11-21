1. Configure the Chilean company and its DTE journals as usual.
2. In the shop, after the delivery step, the customer picks **Boleta** or
   **Factura**. A factura asks for the RUT, business name and giro; the
   billing address entered at checkout is used as the receiver address.
3. Confirm and pay the order. The invoice created from it, by hand or by the
   automatic invoicing setting, already carries the DTE type and receiver.
   Issue it with **Issue DTE**.

The choice and the factura data are shown in the *Other Info* tab of the
sales order. Orders without a choice, for example from express checkout, are
invoiced as boletas. Existing partners are never overwritten by checkout data:
only their empty fiscal fields are completed.
