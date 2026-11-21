Issues the Chilean electronic settlement invoice (DTE 43, *liquidación-factura
electrónica*). A commission agent or consignee (*mandatario*, *comisionista*)
issues it to its principal (*mandante*) to settle the sales or purchases made on
the principal's behalf and the agent's commissions and other charges.

**Accounting model.** The document is issued from a posted vendor bill
(`in_invoice`) of the principal, as the purchase invoice DTE 46 is. For the
agent the settlement is a purchase of the settled operations from the
principal, offset by the commission it charges, and the bill total is exactly
what the agent owes the principal:

* settled operations are positive lines with the 19% purchase VAT (or no VAT
  for exempt operations) and the SII type of the document that supported each
  operation (*Settled Document Type*, `TpoDocLiq`: 33, 39, 99, ...);
* each commission or other charge is a negative line flagged as an *Agent
  Charge* (`C` commission or `O` other charge) with the 19% sales VAT, which is
  the agent's own output VAT, or no VAT for an exempt charge.

A customer invoice cannot hold this document: its total, the amount payable to
the principal, would be negative.

**XML.** DTE_v10 defines the 43 as its own `Liquidacion` payload. Each `Detalle`
carries `TpoDocLiq`; one `Comisiones` line per charge follows the references
(`TipoMovim`, `Glosa`, `ValComNeto`, `ValComExe`, `ValComIVA`); `Totales`
reports `MntNeto`, `MntExe`, `TasaIVA`, `IVA` of the settled operations, the
`Comisiones` totals and `MntTotal`. Per field 124 of the SII *Formato
Documentos Tributarios Electrónicos* (v2.5, 2026-02):

`MntTotal = MntNeto + MntExe + IVA - ValComNeto - ValComIVA - ValComExe`

`VlrPagar` is optional and not emitted. The folio, TED, signature and the SII
submission in Demo, Certification and Production reuse the shared lifecycle of
`l10n_cl_dte_account`.
