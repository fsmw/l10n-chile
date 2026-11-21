Issues the Chilean electronic purchase invoice (DTE 46, *factura de compra
electrónica*) from a posted vendor bill. The company, as buyer, issues the
document on behalf of a seller that does not issue invoices and retains the
whole VAT.

The DTE states the company as Emisor and the supplier as Receptor. Detail
lines subject to the retention carry `CodImpAdic` 15, and `Totales` reports
`MntNeto`, `MntExe`, `TasaIVA` 19, `IVA`, one `ImptoReten` (`TipoImp` 15,
`TasaImp` 19, the retained VAT) and `MntTotal`, the amount the buyer pays the
seller: net plus exempt plus VAT minus the retained VAT.

The document reuses the shared issuance lifecycle of `l10n_cl_dte_account`:
CAF folio, TED, XML signature, validation and the SII submission in Demo,
Certification and Production.

This version supports only the total VAT retention (SII code 15 at 19%).
Partial retentions (SII codes 30 to 48), second category withholdings and
additional taxes are refused.
